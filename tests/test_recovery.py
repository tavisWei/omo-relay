from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import pytest

from omo_task_queue.dispatcher import Dispatcher, LaunchAdapter, TaskResult
from omo_task_queue.recovery import RecoveryManager, RecoveryResult
from omo_task_queue.state import ExecutionMode, Task, TaskStatus
from omo_task_queue.store import Store


class FakeStore(Store):
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def add_task(self, task: Task) -> None:
        if task.id in self._tasks:
            raise KeyError(f"Task already exists: {task.id}")
        self._tasks[task.id] = task

    def get_task(self, task_id: str, project_path: str = "") -> Optional[Task]:
        task = self._tasks.get(task_id)
        if task is None or task.project_path != project_path:
            return None
        return task

    def update_task(self, task: Task) -> None:
        if task.id not in self._tasks:
            raise KeyError(f"Task not found: {task.id}")
        self._tasks[task.id] = task

    def delete_task(self, task_id: str, project_path: str = "") -> None:
        task = self.get_task(task_id, project_path=project_path)
        if task is not None:
            self._tasks.pop(task_id, None)

    def get_next_pending(self, project_path: str = "") -> Optional[Task]:
        pending = [
            t
            for t in self._tasks.values()
            if t.project_path == project_path and t.status == TaskStatus.PENDING
        ]
        if not pending:
            return None
        return sorted(pending, key=lambda t: (-t.order, t.created_at))[0]

    def claim_next(self, project_path: str = "") -> Optional[Task]:
        task = self.get_next_pending(project_path=project_path)
        if task is None:
            return None
        task.status = TaskStatus.RUNNING
        return task

    def list_tasks(
        self, status: Optional[TaskStatus] = None, project_path: str = ""
    ) -> List[Task]:
        tasks = [t for t in self._tasks.values() if t.project_path == project_path]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    def get_running_task(self, project_path: str = "") -> Optional[Task]:
        for t in self._tasks.values():
            if t.project_path == project_path and t.status == TaskStatus.RUNNING:
                return t
        return None

    def reorder_task(
        self, task_id: str, new_order: int, project_path: str = ""
    ) -> None:
        task = self.get_task(task_id, project_path=project_path)
        if task is not None:
            task.order = new_order
            task.updated_at = datetime.utcnow()

    def update_status(
        self, task_id: str, status: TaskStatus, project_path: str = ""
    ) -> None:
        task = self.get_task(task_id, project_path=project_path)
        if task is not None:
            task.status = status
            task.updated_at = datetime.utcnow()
            if status in (TaskStatus.DONE, TaskStatus.SKIPPED):
                task.completed_at = task.updated_at


class FakeAdapter:
    def __init__(self, result: TaskResult) -> None:
        self.result = result
        self.launched: list[Task] = []

    def launch(self, task: Task) -> TaskResult:
        self.launched.append(task)
        return self.result


def make_task(
    task_id: str,
    mode: ExecutionMode = ExecutionMode.ONE_SHOT,
    status: TaskStatus = TaskStatus.PENDING,
    retry_count: int = 0,
    max_retries: int = 3,
) -> Task:
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        prompt=f"Prompt {task_id}",
        mode=mode,
        project_path="",
        status=status,
        retry_count=retry_count,
        max_retries=max_retries,
    )


@pytest.fixture
def store():
    return FakeStore()


class FakeSuccessAdapter:
    def launch(self, task: Task) -> TaskResult:
        return TaskResult(success=True, output=f"success for {task.id}")


@pytest.fixture
def dispatcher(store):
    return Dispatcher(
        store,
        adapters={
            ExecutionMode.ONE_SHOT: FakeSuccessAdapter(),
            ExecutionMode.ULW_LOOP: FakeSuccessAdapter(),
            ExecutionMode.RALPH_LOOP: FakeSuccessAdapter(),
        },
    )


@pytest.fixture
def recovery():
    return RecoveryManager()


class TestRecoveryRunningTasks:
    def test_running_task_becomes_retry_wait(self, store, dispatcher, recovery):
        task = make_task("t1", status=TaskStatus.RUNNING)
        store.add_task(task)

        result = recovery.recover(store, dispatcher)

        assert len(result.recovered_running) == 1
        assert result.recovered_running[0].id == "t1"
        assert task.status == TaskStatus.RETRY_WAIT
        assert task.error_message == "Interrupted by shutdown"

    def test_running_task_preserves_existing_error(self, store, dispatcher, recovery):
        task = make_task("t1", status=TaskStatus.RUNNING)
        task.error_message = "Existing error"
        store.add_task(task)

        recovery.recover(store, dispatcher)

        assert task.error_message == "Existing error"

    def test_multiple_running_tasks_all_recovered(self, store, dispatcher, recovery):
        t1 = make_task("t1", status=TaskStatus.RUNNING)
        t2 = make_task("t2", status=TaskStatus.RUNNING)
        store.add_task(t1)
        store.add_task(t2)

        result = recovery.recover(store, dispatcher)

        assert len(result.recovered_running) == 2
        assert t1.status == TaskStatus.RETRY_WAIT
        assert t2.status == TaskStatus.RETRY_WAIT

    def test_running_task_persisted_to_store(self, store, dispatcher, recovery):
        task = make_task("t1", status=TaskStatus.RUNNING)
        store.add_task(task)

        recovery.recover(store, dispatcher)

        persisted = store.get_task("t1")
        assert persisted is not None
        assert persisted.status == TaskStatus.RETRY_WAIT


class TestRecoveryRetryTasks:
    def test_retry_task_under_max_gets_dispatched(self, store, dispatcher, recovery):
        task = make_task("t1", status=TaskStatus.RETRY_WAIT, retry_count=1)
        store.add_task(task)

        result = recovery.recover(store, dispatcher)

        assert len(result.dispatched) == 1
        assert result.dispatched[0].id == "t1"
        assert task.status == TaskStatus.RUNNING

    def test_retry_task_at_max_gets_skipped(self, store, dispatcher, recovery):
        task = make_task(
            "t1", status=TaskStatus.RETRY_WAIT, retry_count=3, max_retries=3
        )
        store.add_task(task)

        result = recovery.recover(store, dispatcher)

        assert len(result.skipped) == 1
        assert result.skipped[0].id == "t1"
        assert task.status == TaskStatus.RETRY_WAIT

    def test_retry_task_zero_retries_dispatched(self, store, dispatcher, recovery):
        task = make_task(
            "t1", status=TaskStatus.RETRY_WAIT, retry_count=0, max_retries=3
        )
        store.add_task(task)

        result = recovery.recover(store, dispatcher)

        assert len(result.dispatched) == 1
        assert task.status == TaskStatus.RUNNING

    def test_retry_task_persisted_before_dispatch(self, store, recovery):
        task = make_task("t1", status=TaskStatus.RETRY_WAIT, retry_count=1)
        store.add_task(task)
        dispatcher = Dispatcher(
            store,
            adapters={
                ExecutionMode.ONE_SHOT: FakeSuccessAdapter(),
                ExecutionMode.ULW_LOOP: FakeSuccessAdapter(),
                ExecutionMode.RALPH_LOOP: FakeSuccessAdapter(),
            },
        )

        recovery.recover(store, dispatcher)

        persisted = store.get_task("t1")
        assert persisted is not None
        assert persisted.status == TaskStatus.RUNNING


class TestRecoveryNoActiveTasks:
    def test_no_tasks_returns_empty_result(self, store, dispatcher, recovery):
        result = recovery.recover(store, dispatcher)

        assert result.recovered_running == []
        assert result.recovered_retry == []
        assert result.dispatched == []
        assert result.skipped == []

    def test_only_pending_tasks_ignored(self, store, dispatcher, recovery):
        task = make_task("t1", status=TaskStatus.PENDING)
        store.add_task(task)

        result = recovery.recover(store, dispatcher)

        assert result.recovered_running == []
        assert result.dispatched == []
        assert result.skipped == []

    def test_only_done_tasks_ignored(self, store, dispatcher, recovery):
        task = make_task("t1", status=TaskStatus.DONE)
        store.add_task(task)

        result = recovery.recover(store, dispatcher)

        assert result.recovered_running == []
        assert result.dispatched == []
        assert result.skipped == []


class TestRecoveryDuplicatePrevention:
    def test_concurrent_recovery_raises(self, store, dispatcher, recovery):
        task = make_task("t1", status=TaskStatus.RUNNING)
        store.add_task(task)

        recovery._recovery_in_progress = True

        with pytest.raises(RuntimeError, match="Recovery already in progress"):
            recovery.recover(store, dispatcher)

    def test_recovery_flag_cleared_after_success(self, store, dispatcher, recovery):
        task = make_task("t1", status=TaskStatus.RUNNING)
        store.add_task(task)

        recovery.recover(store, dispatcher)

        assert recovery.recovery_in_progress is False

    def test_recovery_flag_cleared_after_empty_recovery(
        self, store, dispatcher, recovery
    ):
        recovery.recover(store, dispatcher)

        assert recovery.recovery_in_progress is False

    def test_second_recovery_allowed_after_first_completes(
        self, store, dispatcher, recovery
    ):
        task = make_task("t1", status=TaskStatus.RUNNING)
        store.add_task(task)

        recovery.recover(store, dispatcher)
        result = recovery.recover(store, dispatcher)

        assert len(result.recovered_running) == 0
        assert len(result.dispatched) == 1


class TestRecoveryMixedState:
    def test_running_and_retry_tasks_together(self, store, dispatcher, recovery):
        running = make_task("running", status=TaskStatus.RUNNING)
        retry = make_task("retry", status=TaskStatus.RETRY_WAIT, retry_count=1)
        store.add_task(running)
        store.add_task(retry)

        result = recovery.recover(store, dispatcher)

        assert len(result.recovered_running) == 1
        assert len(result.dispatched) == 1
        assert running.status == TaskStatus.RETRY_WAIT
        assert retry.status == TaskStatus.RUNNING

    def test_complex_mixed_state(self, store, recovery):
        t1 = make_task("t1", status=TaskStatus.RUNNING)
        t2 = make_task("t2", status=TaskStatus.RUNNING)
        t3 = make_task("t3", status=TaskStatus.RETRY_WAIT, retry_count=1)
        t4 = make_task("t4", status=TaskStatus.RETRY_WAIT, retry_count=3, max_retries=3)
        t5 = make_task("t5", status=TaskStatus.PENDING)
        store.add_task(t1)
        store.add_task(t2)
        store.add_task(t3)
        store.add_task(t4)
        store.add_task(t5)

        dispatcher = Dispatcher(
            store,
            adapters={
                ExecutionMode.ONE_SHOT: FakeSuccessAdapter(),
                ExecutionMode.ULW_LOOP: FakeSuccessAdapter(),
                ExecutionMode.RALPH_LOOP: FakeSuccessAdapter(),
            },
        )
        result = recovery.recover(store, dispatcher)

        assert len(result.recovered_running) == 2
        assert len(result.dispatched) == 1
        assert len(result.skipped) == 1
        assert t1.status == TaskStatus.RETRY_WAIT
        assert t2.status == TaskStatus.RETRY_WAIT
        assert t3.status == TaskStatus.RUNNING
        assert t4.status == TaskStatus.RETRY_WAIT
        assert t5.status == TaskStatus.PENDING


class TestRecoveryResultStructure:
    def test_result_is_dataclass(self):
        result = RecoveryResult()
        assert hasattr(result, "recovered_running")
        assert hasattr(result, "recovered_retry")
        assert hasattr(result, "dispatched")
        assert hasattr(result, "skipped")

    def test_result_defaults_empty(self):
        result = RecoveryResult()
        assert result.recovered_running == []
        assert result.recovered_retry == []
        assert result.dispatched == []
        assert result.skipped == []

    def test_result_collects_tasks(self):
        task = make_task("t1")
        result = RecoveryResult(
            recovered_running=[task],
            dispatched=[task],
        )
        assert result.recovered_running == [task]
        assert result.dispatched == [task]
