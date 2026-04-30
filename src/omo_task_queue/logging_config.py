from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from omo_task_queue.state import ExecutionMode, Task, TaskStatus

AUDIT_LOGGER_NAME = "omo_task_queue.audit"
DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(
    log_path: Optional[str | Path] = None,
    level: int = logging.INFO,
) -> None:
    root_logger = logging.getLogger("omo_task_queue")
    root_logger.setLevel(level)

    if root_logger.handlers:
        root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
    root_logger.addHandler(console_handler)

    audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False

    if audit_logger.handlers:
        audit_logger.handlers.clear()

    if log_path is not None:
        log_path = Path(log_path)
        if log_path.is_dir():
            log_path = log_path / "audit.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(str(log_path), mode="a")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        audit_logger.addHandler(file_handler)
    else:
        audit_handler = logging.StreamHandler(sys.stdout)
        audit_handler.setLevel(logging.INFO)
        audit_handler.setFormatter(logging.Formatter("%(message)s"))
        audit_logger.addHandler(audit_handler)


@dataclass(frozen=True)
class TransitionRecord:
    timestamp: str
    task_id: str
    from_status: str
    to_status: str
    reason: str
    mode: str

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "reason": self.reason,
            "mode": self.mode,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


class AuditLogger:
    def __init__(self, logger_name: str = AUDIT_LOGGER_NAME) -> None:
        self._logger = logging.getLogger(logger_name)

    def log_transition(
        self,
        task: Task,
        from_status: TaskStatus,
        to_status: TaskStatus,
        reason: str = "",
    ) -> TransitionRecord:
        record = TransitionRecord(
            timestamp=datetime.utcnow().isoformat(),
            task_id=task.id,
            from_status=from_status.value,
            to_status=to_status.value,
            reason=reason or "state_change",
            mode=task.mode.value,
        )
        self._logger.info(record.to_json())
        return record

    def log_dispatch(self, task: Task) -> TransitionRecord:
        return self.log_transition(
            task,
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            reason="dispatch",
        )

    def log_completion(self, task: Task) -> TransitionRecord:
        return self.log_transition(
            task,
            TaskStatus.RUNNING,
            TaskStatus.DONE,
            reason="completion",
        )

    def log_retry(self, task: Task) -> TransitionRecord:
        return self.log_transition(
            task,
            TaskStatus.RUNNING,
            TaskStatus.RETRY_WAIT,
            reason="retry",
        )

    def log_skip(self, task: Task) -> TransitionRecord:
        return self.log_transition(
            task,
            task.status,
            TaskStatus.SKIPPED,
            reason="skip",
        )

    def log_failure(self, task: Task) -> TransitionRecord:
        return self.log_transition(
            task,
            TaskStatus.RUNNING,
            TaskStatus.RETRY_WAIT,
            reason="failure",
        )
