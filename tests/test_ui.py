"""Tests for the UI panel backend contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pytest

from omo_task_queue.state import ExecutionMode, Task, TaskStatus
from omo_task_queue.ui.panel import (
    AddTaskRequest,
    PanelHandler,
    QueueItem,
    ReorderRequest,
    RunningTaskInfo,
    TaskActionRequest,
    TestNotificationRequest,
    UIAction,
    UIResponse,
)


@dataclass
class FakeStore:
    _tasks: dict[str, Task] = field(default_factory=dict)
    _counter: int = 0

    def add_task(self, task: Task) -> None:
        self._counter += 1
        task.id = f"task-{self._counter}"
        task.order = self._counter
        self._tasks[task.id] = task

    def list_tasks(
        self, status: Optional[TaskStatus] = None, project_path: str = ""
    ) -> list[Task]:
        tasks = [
            task for task in self._tasks.values() if task.project_path == project_path
        ]
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return sorted(
            tasks,
            key=lambda t: (t.order, t.created_at),
        )

    def list_active_tasks(self, project_path: str = "") -> list[Task]:
        priority = {
            TaskStatus.RUNNING: 0,
            TaskStatus.RETRY_WAIT: 1,
            TaskStatus.PENDING: 2,
        }
        tasks = [
            task
            for task in self._tasks.values()
            if task.project_path == project_path
            if task.status
            in {TaskStatus.RUNNING, TaskStatus.RETRY_WAIT, TaskStatus.PENDING}
        ]
        return sorted(
            tasks,
            key=lambda t: (priority[t.status], -t.order, t.created_at),
        )

    def list_completed_tasks(self, project_path: str = "") -> list[Task]:
        tasks = [
            task
            for task in self._tasks.values()
            if task.project_path == project_path
            if task.status in {TaskStatus.DONE, TaskStatus.SKIPPED}
        ]
        return sorted(
            tasks,
            key=lambda t: t.completed_at or datetime.min,
            reverse=True,
        )

    def get_task(self, task_id: str, project_path: str = "") -> Optional[Task]:
        task = self._tasks.get(task_id)
        if task is None or task.project_path != project_path:
            return None
        return task

    def update_task(self, task: Task) -> None:
        self._tasks[task.id] = task

    def get_running_task(self, project_path: str = "") -> Optional[Task]:
        for task in self._tasks.values():
            if task.project_path == project_path and task.status == TaskStatus.RUNNING:
                return task
        return None

    def reorder_task(
        self, task_id: str, new_order: int, project_path: str = ""
    ) -> None:
        task = self.get_task(task_id, project_path=project_path)
        if task is not None:
            task.order = new_order
            task.updated_at = datetime.utcnow()

    def delete_task(self, task_id: str, project_path: str = "") -> None:
        task = self.get_task(task_id, project_path=project_path)
        if task is not None:
            self._tasks.pop(task_id, None)

    def update_status(
        self, task_id: str, status: TaskStatus, project_path: str = ""
    ) -> None:
        task = self.get_task(task_id, project_path=project_path)
        if task is not None:
            task.status = status
            task.updated_at = datetime.utcnow()
            if status in (TaskStatus.DONE, TaskStatus.SKIPPED):
                task.completed_at = task.updated_at


@dataclass
class FakeTmuxTarget:
    attach_command: str = "tmux attach -t omo-test"


@dataclass
class FakeTmuxTargetStore:
    attach_command: str = "tmux attach -t omo-test"

    def load(self):
        return FakeTmuxTarget(self.attach_command)


@dataclass
class FakeNotifier:
    last_test_recipient: Optional[str] = None

    def send_test(self, recipient: Optional[str] = None) -> None:
        self.last_test_recipient = recipient


@dataclass
class FakeQueueStarter:
    calls: int = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def handler(store):
    return PanelHandler(store, tmux_target_store=FakeTmuxTargetStore(), project_path="")


@pytest.fixture
def handler_with_notifier(store):
    return PanelHandler(
        store,
        notifier=FakeNotifier(),
        tmux_target_store=FakeTmuxTargetStore(),
        project_path="",
    )


@pytest.fixture
def queue_starter():
    return FakeQueueStarter()


@pytest.fixture
def handler_with_queue_starter(store, queue_starter):
    return PanelHandler(
        store,
        queue_starter=queue_starter,
        tmux_target_store=FakeTmuxTargetStore(),
        project_path="",
    )


def test_ui_action_values():
    assert UIAction.ADD_TASK.value == "add_task"
    assert UIAction.LIST_QUEUE.value == "list_queue"
    assert UIAction.GET_RUNNING.value == "get_running"
    assert UIAction.REORDER.value == "reorder"
    assert UIAction.DELETE.value == "delete"
    assert UIAction.SKIP.value == "skip"
    assert UIAction.DONE.value == "done"
    assert UIAction.TEST_NOTIFICATION.value == "test_notification"


def test_add_task(handler):
    req = AddTaskRequest(
        title="Test", prompt="Do something", mode=ExecutionMode.ONE_SHOT
    )
    resp = handler.handle(UIAction.ADD_TASK, req)

    assert resp.success is True
    assert isinstance(resp.data, QueueItem)
    assert resp.data.title == "Test"
    assert resp.data.prompt == "Do something"
    assert resp.data.status == TaskStatus.PENDING
    assert resp.data.mode == ExecutionMode.ONE_SHOT
    assert resp.data.tmux_attach_command == "tmux attach -t omo-test"


def test_add_task_defaults(handler):
    req = AddTaskRequest(prompt="prompt")
    resp = handler.handle(UIAction.ADD_TASK, req)

    assert resp.data.title == "prompt"
    assert resp.data.mode == ExecutionMode.ONE_SHOT
    assert resp.data.max_retries == 3


def test_add_task_derives_title_from_prompt(handler):
    req = AddTaskRequest(
        prompt="This is a longer prompt title summary that should truncate"
    )
    resp = handler.handle(UIAction.ADD_TASK, req)

    assert resp.success is True
    assert resp.data.title == "This is a longer prompt…"


def test_add_task_starts_queue(handler_with_queue_starter, queue_starter):
    req = AddTaskRequest(title="Auto", prompt="p")
    resp = handler_with_queue_starter.handle(UIAction.ADD_TASK, req)
    assert resp.success is True
    assert queue_starter.calls == 1


def test_list_queue_empty(handler):
    resp = handler.handle(UIAction.LIST_QUEUE)
    assert resp.success is True
    assert resp.data == {"active": [], "completed": []}


def test_list_queue_ordered(handler, store):
    from omo_task_queue.state import Task

    store.add_task(Task(id="a", title="A", prompt="pA", mode=ExecutionMode.ONE_SHOT))
    store.add_task(Task(id="b", title="B", prompt="pB", mode=ExecutionMode.ONE_SHOT))
    store.add_task(
        Task(
            id="c",
            title="C",
            prompt="pC",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.DONE,
        )
    )
    store.update_status("c", TaskStatus.DONE)

    resp = handler.handle(UIAction.LIST_QUEUE)
    assert len(resp.data["active"]) == 2
    assert resp.data["active"][0].title == "B"
    assert resp.data["active"][1].title == "A"
    assert resp.data["active"][0].prompt == "pB"
    assert resp.data["active"][0].tmux_attach_command == "tmux attach -t omo-test"
    assert len(resp.data["completed"]) == 1
    assert resp.data["completed"][0].title == "C"


def test_get_running_none(handler):
    resp = handler.handle(UIAction.GET_RUNNING)
    assert resp.success is True
    assert resp.data is None


def test_get_running_active(handler, store):
    from omo_task_queue.state import Task

    task = Task(id="run-1", title="Run", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)
    store.update_status(task.id, TaskStatus.RUNNING)

    resp = handler.handle(UIAction.GET_RUNNING)
    assert resp.success is True
    assert isinstance(resp.data, RunningTaskInfo)
    assert resp.data.id == task.id
    assert resp.data.title == "Run"


def test_reorder(handler, store):
    from omo_task_queue.state import Task

    task = Task(id="reorder-1", title="T", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)
    req = ReorderRequest(task_id=task.id, new_order=99)
    resp = handler.handle(UIAction.REORDER, req)

    assert resp.success is True
    assert store.get_task(task.id).order == 99


def test_reorder_missing_task(handler):
    req = ReorderRequest(task_id="nope", new_order=0)
    resp = handler.handle(UIAction.REORDER, req)

    assert resp.success is False
    assert "not found" in resp.error.lower()


def test_delete(handler, store):
    from omo_task_queue.state import Task

    task = Task(id="del-1", title="Del", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)
    req = TaskActionRequest(task_id=task.id)
    resp = handler.handle(UIAction.DELETE, req)

    assert resp.success is True
    assert store.get_task(task.id) is None


def test_delete_missing(handler):
    req = TaskActionRequest(task_id="missing")
    resp = handler.handle(UIAction.DELETE, req)

    assert resp.success is False
    assert "not found" in resp.error.lower()


def test_skip(handler, store):
    from omo_task_queue.state import Task

    task = Task(id="skip-1", title="Skip", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)
    req = TaskActionRequest(task_id=task.id)
    resp = handler.handle(UIAction.SKIP, req)

    assert resp.success is True
    assert store.get_task(task.id).status == TaskStatus.SKIPPED


def test_skip_starts_queue(handler_with_queue_starter, store, queue_starter):
    from omo_task_queue.state import Task

    task = Task(id="skip-queue", title="Skip", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)
    req = TaskActionRequest(task_id=task.id)
    resp = handler_with_queue_starter.handle(UIAction.SKIP, req)
    assert resp.success is True
    assert queue_starter.calls == 1


def test_skip_missing(handler):
    req = TaskActionRequest(task_id="missing")
    resp = handler.handle(UIAction.SKIP, req)

    assert resp.success is False
    assert "not found" in resp.error.lower()


def test_done(handler, store):
    from omo_task_queue.state import Task

    task = Task(id="done-1", title="Done", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)
    req = TaskActionRequest(task_id=task.id)
    resp = handler.handle(UIAction.DONE, req)

    assert resp.success is True
    assert store.get_task(task.id).status == TaskStatus.DONE


def test_done_starts_queue(handler_with_queue_starter, store, queue_starter):
    from omo_task_queue.state import Task

    task = Task(id="done-queue", title="Done", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)
    req = TaskActionRequest(task_id=task.id)
    resp = handler_with_queue_starter.handle(UIAction.DONE, req)
    assert resp.success is True
    assert queue_starter.calls == 1


def test_done_missing(handler):
    req = TaskActionRequest(task_id="missing")
    resp = handler.handle(UIAction.DONE, req)

    assert resp.success is False
    assert "not found" in resp.error.lower()


def test_retry_moves_task_back_to_pending(handler, store):
    task = Task(id="retry-1", title="Retry", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)
    store.update_status(task.id, TaskStatus.SKIPPED)
    task.retry_count = 2
    task.error_message = "boom"

    resp = handler.retry(TaskActionRequest(task_id=task.id))

    assert resp.success is True
    updated = store.get_task(task.id)
    assert updated is not None
    assert updated.status == TaskStatus.PENDING
    assert updated.retry_count == 0
    assert updated.error_message is None
    assert updated.completed_at is None


def test_retry_rejects_running_task(handler, store):
    task = Task(id="retry-run", title="Retry", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)
    store.update_status(task.id, TaskStatus.RUNNING)

    resp = handler.retry(TaskActionRequest(task_id=task.id))

    assert resp.success is False
    assert "cannot be retried" in resp.error.lower()


def test_test_notification_no_notifier(handler):
    req = TestNotificationRequest(recipient="a@b.com")
    resp = handler.handle(UIAction.TEST_NOTIFICATION, req)

    assert resp.success is False
    assert "not configured" in resp.error.lower()


def test_test_notification_with_notifier(handler_with_notifier):
    req = TestNotificationRequest(recipient="a@b.com")
    resp = handler_with_notifier.handle(UIAction.TEST_NOTIFICATION, req)

    assert resp.success is True
    assert handler_with_notifier._notifier.last_test_recipient == "a@b.com"


def test_unknown_action(handler):
    class FakeAction:
        value = "fake"

    resp = handler.handle(FakeAction())  # type: ignore[arg-type]
    assert resp.success is False
    assert "Unknown action" in resp.error


def test_direct_methods(handler, store):
    from omo_task_queue.state import Task

    task = Task(id="direct-1", title="Direct", prompt="p", mode=ExecutionMode.ONE_SHOT)
    store.add_task(task)

    r = handler.delete(TaskActionRequest(task_id=task.id))
    assert r.success is True

    r = handler.skip(TaskActionRequest(task_id="gone"))
    assert r.success is False

    r = handler.done(TaskActionRequest(task_id="gone"))
    assert r.success is False

    r = handler.reorder(ReorderRequest(task_id="gone", new_order=0))
    assert r.success is False
