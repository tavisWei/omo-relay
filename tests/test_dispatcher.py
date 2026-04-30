import pytest
from datetime import datetime
from typing import List, Optional

from omo_task_queue.dispatcher import (
    Dispatcher,
    LaunchAdapter,
    OneShotAdapter,
    RalphLoopAdapter,
    RetryManager,
    TaskResult,
    ULWLoopAdapter,
)
from omo_task_queue.state import ExecutionMode, StateMachine, Task, TaskStatus
from omo_task_queue.store import Store


class FakeStore(Store):
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._claim_calls: int = 0

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
        self._claim_calls += 1
        task = self.get_next_pending(project_path=project_path)
        if task is None:
            return None
        task.status = TaskStatus.RUNNING
        task.updated_at = datetime.utcnow()
        return task

    def list_tasks(
        self, status: Optional[TaskStatus] = None, project_path: str = ""
    ) -> List[Task]:
        tasks = [t for t in self._tasks.values() if t.project_path == project_path]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: (t.order, t.created_at))

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


class FailingAdapter:
    def launch(self, task: Task) -> TaskResult:
        raise RuntimeError("Simulated adapter failure")


class CustomAdapter:
    def __init__(self, result: TaskResult) -> None:
        self.result = result
        self.launched: list[Task] = []

    def launch(self, task: Task) -> TaskResult:
        self.launched.append(task)
        return self.result


class FakeRuntimeClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.commands: list[tuple[str, str]] = []
        self.created_titles: list[Optional[str]] = []

    def send_prompt(self, text: str, title: Optional[str] = None) -> str:
        self.prompts.append(text)
        self.created_titles.append(title)
        return f"sess-{len(self.prompts)}"

    def send_command(
        self, command: str, args: str = "", title: Optional[str] = None
    ) -> str:
        self.commands.append((command, args))
        self.created_titles.append(title)
        return f"sess-cmd-{len(self.commands)}"


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def dispatcher(store):
    return Dispatcher(
        store,
        adapters={
            ExecutionMode.ONE_SHOT: CustomAdapter(
                TaskResult(success=True, launched=True)
            ),
            ExecutionMode.ULW_LOOP: CustomAdapter(
                TaskResult(success=True, launched=True)
            ),
            ExecutionMode.RALPH_LOOP: CustomAdapter(
                TaskResult(success=True, launched=True)
            ),
        },
    )


def make_task(
    task_id: str, mode: ExecutionMode, status: TaskStatus = TaskStatus.RUNNING
) -> Task:
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        prompt=f"Prompt {task_id}",
        mode=mode,
        project_path="",
        status=status,
    )


class TestOneShotAdapter:
    def test_launch_sends_prompt(self):
        client = FakeRuntimeClient()
        adapter = OneShotAdapter(client)
        task = make_task("t1", ExecutionMode.ONE_SHOT)
        result = adapter.launch(task)
        assert result.success is True
        assert client.prompts == ["Prompt t1"]
        assert result.session_id == "sess-1"
        assert "t1" in result.output

    def test_launch_without_client_fails(self):
        adapter = OneShotAdapter()
        task = make_task("t1", ExecutionMode.ONE_SHOT)
        result = adapter.launch(task)
        assert result.success is False
        assert "No runtime client configured" in result.error_message


class TestULWLoopAdapter:
    def test_launch_sends_command(self):
        client = FakeRuntimeClient()
        adapter = ULWLoopAdapter(client)
        task = make_task("t1", ExecutionMode.ULW_LOOP)
        result = adapter.launch(task)
        assert result.success is True
        assert client.commands == [("ulw-loop", "Prompt t1")]
        assert result.session_id == "sess-cmd-1"
        assert "t1" in result.output

    def test_launch_without_client_fails(self):
        adapter = ULWLoopAdapter()
        task = make_task("t1", ExecutionMode.ULW_LOOP)
        result = adapter.launch(task)
        assert result.success is False
        assert "No runtime client configured" in result.error_message


class TestRalphLoopAdapter:
    def test_launch_sends_command(self):
        client = FakeRuntimeClient()
        adapter = RalphLoopAdapter(client)
        task = make_task("t1", ExecutionMode.RALPH_LOOP)
        result = adapter.launch(task)
        assert result.success is True
        assert client.commands == [("ralph-loop", "Prompt t1")]
        assert result.session_id == "sess-cmd-1"
        assert "t1" in result.output

    def test_launch_without_client_fails(self):
        adapter = RalphLoopAdapter()
        task = make_task("t1", ExecutionMode.RALPH_LOOP)
        result = adapter.launch(task)
        assert result.success is False
        assert "No runtime client configured" in result.error_message


class TestDispatcherDispatch:
    def test_one_shot_mode(self, dispatcher, store):
        task = make_task("t1", ExecutionMode.ONE_SHOT)
        store.add_task(task)
        result = dispatcher.dispatch(task)
        assert result.success is True
        assert result.launched is True
        assert task.status == TaskStatus.RUNNING

    def test_ulw_loop_mode(self, dispatcher, store):
        task = make_task("t1", ExecutionMode.ULW_LOOP)
        store.add_task(task)
        result = dispatcher.dispatch(task)
        assert result.success is True
        assert result.launched is True
        assert task.status == TaskStatus.RUNNING

    def test_ralph_loop_mode(self, dispatcher, store):
        task = make_task("t1", ExecutionMode.RALPH_LOOP)
        store.add_task(task)
        result = dispatcher.dispatch(task)
        assert result.success is True
        assert result.launched is True
        assert task.status == TaskStatus.RUNNING

    def test_unknown_mode_returns_failure(self, store):
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ULW_LOOP: CustomAdapter(TaskResult(success=True))},
        )
        task = make_task("t1", ExecutionMode.ONE_SHOT)
        store.add_task(task)
        result = dispatcher.dispatch(task)
        assert result.success is False
        assert "No adapter registered" in result.error_message

    def test_adapter_exception_caught(self, store):
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        task = make_task("t1", ExecutionMode.ONE_SHOT)
        store.add_task(task)
        result = dispatcher.dispatch(task)
        assert result.success is False
        assert "Simulated adapter failure" in result.error_message

    def test_no_duplicate_dispatch(self, store):
        slow_adapter = CustomAdapter(TaskResult(success=True))
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: slow_adapter},
        )
        task1 = make_task("t1", ExecutionMode.ONE_SHOT)
        task2 = make_task("t2", ExecutionMode.ONE_SHOT)
        store.add_task(task1)
        store.add_task(task2)

        result2 = dispatcher.dispatch(task2)
        assert result2.success is True

        result1 = dispatcher.dispatch(task1)
        assert result1.success is True

    def test_concurrent_dispatch_blocked(self, store):
        class BlockingAdapter:
            def launch(self, task: Task) -> TaskResult:
                return TaskResult(success=True)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: BlockingAdapter()},
        )
        task1 = make_task("t1", ExecutionMode.ONE_SHOT)
        task2 = make_task("t2", ExecutionMode.ONE_SHOT)
        store.add_task(task1)
        store.add_task(task2)

        dispatcher._currently_running = "other_task"
        result = dispatcher.dispatch(task1)
        assert result.success is False
        assert "Dispatcher busy" in result.error_message


class TestDispatcherAutoAdvance:
    def test_success_advances_queue(self, dispatcher, store):
        task1 = make_task("t1", ExecutionMode.ONE_SHOT, status=TaskStatus.PENDING)
        task2 = make_task("t2", ExecutionMode.ONE_SHOT, status=TaskStatus.PENDING)
        store.add_task(task1)
        store.add_task(task2)

        claimed = store.claim_next()
        assert claimed.id == "t1"
        dispatcher.dispatch(claimed)
        dispatcher.mark_task_completed("t1")
        dispatcher.mark_task_completed("t2")

        assert task1.status == TaskStatus.DONE
        assert task2.status == TaskStatus.DONE

    def test_success_no_pending_does_not_crash(self, dispatcher, store):
        task = make_task("t1", ExecutionMode.ONE_SHOT, status=TaskStatus.PENDING)
        store.add_task(task)
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)
        dispatcher.mark_task_completed("t1")
        assert task.status == TaskStatus.DONE

    def test_failure_blocks_advancement(self, dispatcher, store):
        class FailingOneShot:
            def launch(self, task: Task) -> TaskResult:
                return TaskResult(success=False, error_message="boom")

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingOneShot()},
        )
        task1 = make_task("t1", ExecutionMode.ONE_SHOT, status=TaskStatus.PENDING)
        task2 = make_task("t2", ExecutionMode.ONE_SHOT, status=TaskStatus.PENDING)
        store.add_task(task1)
        store.add_task(task2)

        claimed = store.claim_next()
        dispatcher.dispatch(claimed)

        assert task1.status == TaskStatus.RETRY_WAIT
        assert task2.status == TaskStatus.PENDING

    def test_failure_triggers_retry(self, dispatcher, store):
        class FailingOneShot:
            def launch(self, task: Task) -> TaskResult:
                return TaskResult(success=False, error_message="boom")

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingOneShot()},
        )
        task = make_task("t1", ExecutionMode.ONE_SHOT, status=TaskStatus.PENDING)
        store.add_task(task)
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)

        assert task.status == TaskStatus.RETRY_WAIT
        assert task.retry_count == 1
        assert task.error_message == "boom"

    def test_max_retries_exceeded(self, store):
        class FailingOneShot:
            def launch(self, task: Task) -> TaskResult:
                return TaskResult(success=False, error_message="boom")

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingOneShot()},
        )
        task = make_task("t1", ExecutionMode.ONE_SHOT, status=TaskStatus.PENDING)
        task.max_retries = 2
        store.add_task(task)

        claimed = store.claim_next()
        dispatcher.dispatch(claimed)
        assert task.status == TaskStatus.RETRY_WAIT
        assert task.retry_count == 1

        StateMachine.transition(task, TaskStatus.RUNNING)
        dispatcher.dispatch(task)
        assert task.status == TaskStatus.RETRY_WAIT
        assert task.retry_count == 2

        StateMachine.transition(task, TaskStatus.RUNNING)
        dispatcher.dispatch(task)
        assert task.retry_count == 3
        assert task.status == TaskStatus.RUNNING


class TestDispatcherState:
    def test_currently_running_property(self, dispatcher, store):
        class SlowAdapter:
            def launch(self, task: Task) -> TaskResult:
                return TaskResult(success=True)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: SlowAdapter()},
        )
        task = make_task("t1", ExecutionMode.ONE_SHOT)
        store.add_task(task)

        assert dispatcher.currently_running is None
        dispatcher.dispatch(task)
        assert dispatcher.currently_running is None

    def test_mark_task_completed_updates_status(self, dispatcher, store):
        task = make_task("t1", ExecutionMode.ONE_SHOT)
        store.add_task(task)
        dispatcher.dispatch(task)
        finished = dispatcher.mark_task_completed("t1")
        assert finished is not None
        assert task.status == TaskStatus.DONE

    def test_mark_task_failed_updates_status(self, dispatcher, store):
        task = make_task("t1", ExecutionMode.ONE_SHOT)
        store.add_task(task)
        dispatcher.dispatch(task)
        failed = dispatcher.mark_task_failed("t1", "boom")
        assert failed is not None
        assert task.status == TaskStatus.RETRY_WAIT

    def test_start_next_pending_claims_and_dispatches(self, store):
        custom = CustomAdapter(TaskResult(success=True, launched=True))
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: custom},
        )
        task = make_task("t1", ExecutionMode.ONE_SHOT, status=TaskStatus.PENDING)
        store.add_task(task)
        result = dispatcher.start_next_pending()
        assert result is not None
        assert result.success is True
        assert custom.launched == [task]

    def test_start_next_pending_skips_when_running_exists(self, store):
        custom = CustomAdapter(TaskResult(success=True, launched=True))
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: custom},
        )
        running = make_task("run", ExecutionMode.ONE_SHOT, status=TaskStatus.RUNNING)
        pending = make_task(
            "pending", ExecutionMode.ONE_SHOT, status=TaskStatus.PENDING
        )
        store.add_task(running)
        store.add_task(pending)
        result = dispatcher.start_next_pending()
        assert result is None
        assert custom.launched == []

    def test_custom_adapters_injected(self, store):
        custom = CustomAdapter(TaskResult(success=True))
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: custom},
        )
        task = make_task("t1", ExecutionMode.ONE_SHOT)
        store.add_task(task)
        dispatcher.dispatch(task)
        assert custom.launched == [task]


class TestRetryManager:
    def test_handle_failure_increments_retry(self, store):
        task = make_task("t1", ExecutionMode.ONE_SHOT, status=TaskStatus.RUNNING)
        store.add_task(task)
        rm = RetryManager()
        rm.handle_failure(task, store, "something broke")
        assert task.retry_count == 1
        assert task.status == TaskStatus.RETRY_WAIT
        assert task.error_message == "something broke"

    def test_handle_failure_max_retries(self, store):
        task = make_task("t1", ExecutionMode.ONE_SHOT, status=TaskStatus.RUNNING)
        task.max_retries = 1
        store.add_task(task)
        rm = RetryManager()
        rm.handle_failure(task, store, "fatal")
        assert task.retry_count == 1
        assert task.status == TaskStatus.RETRY_WAIT
        StateMachine.transition(task, TaskStatus.RUNNING)
        rm.handle_failure(task, store, "fatal again")
        assert task.retry_count == 2
        assert task.status == TaskStatus.RUNNING
