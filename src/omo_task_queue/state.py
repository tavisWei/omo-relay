from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    DONE = "done"
    SKIPPED = "skipped"


class ExecutionMode(str, Enum):
    ONE_SHOT = "one_shot"
    ULW_LOOP = "ulw_loop"
    RALPH_LOOP = "ralph_loop"


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    mode: ExecutionMode
    project_path: str = ""
    target_session_id: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    order: int = 0

    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.DONE, TaskStatus.SKIPPED)


class StateMachine:
    _LEGAL: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.PENDING: {TaskStatus.RUNNING},
        TaskStatus.RUNNING: {TaskStatus.DONE, TaskStatus.RETRY_WAIT},
        TaskStatus.RETRY_WAIT: {
            TaskStatus.RUNNING,
            TaskStatus.SKIPPED,
            TaskStatus.DONE,
        },
        TaskStatus.DONE: set(),
        TaskStatus.SKIPPED: set(),
    }

    @classmethod
    def can_transition(cls, current: TaskStatus, target: TaskStatus) -> bool:
        return target in cls._LEGAL.get(current, set())

    @classmethod
    def transition(cls, task: Task, target: TaskStatus) -> None:
        if not cls.can_transition(task.status, target):
            raise ValueError(
                f"Illegal transition: {task.status.value} → {target.value}"
            )

        task.status = target
        task.updated_at = datetime.utcnow()

        if task.is_terminal() and task.completed_at is None:
            task.completed_at = task.updated_at
