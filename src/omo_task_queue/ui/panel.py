"""OMO Task Queue Plugin — UI panel backend contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from omo_task_queue.confirmed_session import (
    ConfirmedSessionStore,
    resolve_confirmed_session_id,
)
from omo_task_queue.state import ExecutionMode, Task, TaskStatus
from omo_task_queue.tmux_target import TmuxTargetStore


class UIAction(str, Enum):
    ADD_TASK = "add_task"
    LIST_QUEUE = "list_queue"
    GET_RUNNING = "get_running"
    REORDER = "reorder"
    DELETE = "delete"
    SKIP = "skip"
    DONE = "done"
    RETRY = "retry"
    TEST_NOTIFICATION = "test_notification"


@dataclass
class AddTaskRequest:
    prompt: str
    title: str = ""
    mode: ExecutionMode = ExecutionMode.ONE_SHOT
    max_retries: int = 3


@dataclass
class ReorderRequest:
    task_id: str
    new_order: int


@dataclass
class TaskActionRequest:
    task_id: str


@dataclass
class _NotificationRequest:
    __test__ = False
    recipient: Optional[str] = None


@dataclass
class UIResponse:
    success: bool
    data: Any = None
    error: Optional[str] = None


@dataclass
class QueueItem:
    id: str
    title: str
    prompt: str
    status: TaskStatus
    mode: ExecutionMode
    retry_count: int
    max_retries: int
    order: int
    created_at: datetime
    updated_at: datetime
    error_message: Optional[str] = None
    tmux_attach_command: Optional[str] = None

    @classmethod
    def from_task(
        cls, task: Task, tmux_attach_command: Optional[str] = None
    ) -> QueueItem:
        return cls(
            id=task.id,
            title=task.title,
            prompt=task.prompt,
            status=task.status,
            mode=task.mode,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            order=task.order,
            created_at=task.created_at,
            updated_at=task.updated_at,
            error_message=task.error_message,
            tmux_attach_command=tmux_attach_command,
        )


@dataclass
class RunningTaskInfo:
    id: str
    title: str
    mode: ExecutionMode
    started_at: datetime
    retry_count: int
    max_retries: int


class PanelHandler:
    def __init__(
        self,
        store: Any,
        notifier: Any = None,
        queue_starter: Any = None,
        tmux_target_store: Any = None,
        project_path: str = "",
        session_resolver: Any = None,
    ) -> None:
        self._store = store
        self._notifier = notifier
        self._queue_starter = queue_starter
        self._tmux_target_store = tmux_target_store
        self._project_path = project_path
        self._session_resolver = session_resolver

    def _maybe_start_queue(self) -> None:
        if self._queue_starter is not None:
            self._queue_starter()

    def handle(self, action: UIAction, payload: Any = None) -> UIResponse:
        handler = _HANDLERS.get(action)
        if handler is None:
            return UIResponse(success=False, error=f"Unknown action: {action.value}")
        try:
            return handler(self, payload)
        except Exception as exc:
            return UIResponse(success=False, error=str(exc))

    def add_task(self, req: AddTaskRequest) -> UIResponse:
        import uuid

        from omo_task_queue.state import Task

        title = self._derive_title(req.prompt, req.title)
        task = Task(
            id=str(uuid.uuid4()),
            title=title,
            prompt=req.prompt,
            mode=req.mode,
            project_path=self._project_path,
            target_session_id=self._resolve_session_id(),
            max_retries=req.max_retries,
        )
        self._store.add_task(task)
        self._maybe_start_queue()
        return UIResponse(
            success=True,
            data=QueueItem.from_task(task, self._tmux_attach_command()),
        )

    def list_queue(self, _payload: Any = None) -> UIResponse:
        tmux_attach_command = self._tmux_attach_command()
        active_tasks = (
            self._store.list_active_tasks(project_path=self._project_path)
            if hasattr(self._store, "list_active_tasks")
            else self._store.list_tasks(project_path=self._project_path)
        )
        completed_tasks = (
            self._store.list_completed_tasks(project_path=self._project_path)
            if hasattr(self._store, "list_completed_tasks")
            else []
        )
        return UIResponse(
            success=True,
            data={
                "active": [
                    QueueItem.from_task(t, tmux_attach_command) for t in active_tasks
                ],
                "completed": [
                    QueueItem.from_task(t, tmux_attach_command) for t in completed_tasks
                ],
            },
        )

    def get_running(self, _payload: Any = None) -> UIResponse:
        task = self._store.get_running_task(project_path=self._project_path)
        if task is None:
            return UIResponse(success=True, data=None)
        info = RunningTaskInfo(
            id=task.id,
            title=task.title,
            mode=task.mode,
            started_at=task.updated_at,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
        )
        return UIResponse(success=True, data=info)

    def reorder(self, req: ReorderRequest) -> UIResponse:
        task = self._store.get_task(req.task_id, project_path=self._project_path)
        if task is None:
            return _not_found(req.task_id)
        self._store.reorder_task(
            req.task_id, req.new_order, project_path=self._project_path
        )
        return UIResponse(success=True)

    def delete(self, req: TaskActionRequest) -> UIResponse:
        task = self._store.get_task(req.task_id, project_path=self._project_path)
        if task is None:
            return _not_found(req.task_id)
        self._store.delete_task(req.task_id, project_path=self._project_path)
        return UIResponse(success=True)

    def skip(self, req: TaskActionRequest) -> UIResponse:
        task = self._store.get_task(req.task_id, project_path=self._project_path)
        if task is None:
            return _not_found(req.task_id)
        self._store.update_status(
            req.task_id, TaskStatus.SKIPPED, project_path=self._project_path
        )
        self._maybe_start_queue()
        return UIResponse(success=True)

    def done(self, req: TaskActionRequest) -> UIResponse:
        task = self._store.get_task(req.task_id, project_path=self._project_path)
        if task is None:
            return _not_found(req.task_id)
        self._store.update_status(
            req.task_id, TaskStatus.DONE, project_path=self._project_path
        )
        self._maybe_start_queue()
        return UIResponse(success=True)

    def retry(self, req: TaskActionRequest) -> UIResponse:
        task = self._store.get_task(req.task_id, project_path=self._project_path)
        if task is None:
            return _not_found(req.task_id)
        if task.status is TaskStatus.RUNNING:
            return UIResponse(success=False, error="Running task cannot be retried")
        existing_tasks = self._store.list_tasks(project_path=self._project_path)
        resolved_session_id = self._resolve_session_id()
        task.order = max((item.order for item in existing_tasks), default=0) + 1
        task.status = TaskStatus.PENDING
        task.retry_count = 0
        task.completed_at = None
        task.error_message = None
        if resolved_session_id is not None:
            task.target_session_id = resolved_session_id
        task.updated_at = datetime.utcnow()
        self._store.update_task(task)
        self._maybe_start_queue()
        return UIResponse(success=True)

    def test_notification(self, req: _NotificationRequest) -> UIResponse:
        if self._notifier is None:
            return UIResponse(success=False, error="Notifier not configured")
        try:
            self._notifier.send_test(recipient=req.recipient)
            return UIResponse(success=True)
        except Exception as exc:
            return UIResponse(success=False, error=str(exc))

    def _tmux_attach_command(self) -> Optional[str]:
        target = self._load_tmux_target()
        return target.attach_command if target else None

    def _load_tmux_target(self):
        confirmed_session_id = None
        if self._project_path:
            confirmed_session_id = resolve_confirmed_session_id(
                self._project_path, self._resolve_session_id()
            )
        if confirmed_session_id is not None:
            short_id = ConfirmedSessionStore.session_short_id(confirmed_session_id)
            target_store = TmuxTargetStore(
                Path(self._project_path) / f".omo_tmux_target.{short_id}.json"
            )
            target = target_store.load()
            if target is not None:
                return target
        if self._tmux_target_store is None:
            return None
        return self._tmux_target_store.load()

    def _resolve_session_id(self) -> Optional[str]:
        if self._session_resolver is None:
            return None
        return self._session_resolver()

    @staticmethod
    def _derive_title(prompt: str, title: str = "") -> str:
        normalized_title = title.strip()
        if normalized_title:
            return normalized_title
        summary = " ".join(prompt.strip().split())
        if len(summary) <= 24:
            return summary
        return f"{summary[:24].rstrip()}…"


_HANDLERS: dict[UIAction, Any] = {
    UIAction.ADD_TASK: PanelHandler.add_task,
    UIAction.LIST_QUEUE: PanelHandler.list_queue,
    UIAction.GET_RUNNING: PanelHandler.get_running,
    UIAction.REORDER: PanelHandler.reorder,
    UIAction.DELETE: PanelHandler.delete,
    UIAction.SKIP: PanelHandler.skip,
    UIAction.DONE: PanelHandler.done,
    UIAction.RETRY: PanelHandler.retry,
    UIAction.TEST_NOTIFICATION: PanelHandler.test_notification,
}


NotificationRequest = _NotificationRequest
NotifyTestRequest = _NotificationRequest
NotificationTestRequest = _NotificationRequest
TestNotificationRequest = _NotificationRequest


def _not_found(task_id: str) -> UIResponse:
    return UIResponse(success=False, error=f"Task not found: {task_id}")
