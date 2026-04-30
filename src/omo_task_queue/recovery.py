"""OMO Task Queue Plugin — Restart recovery and active-task reconciliation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from omo_task_queue.dispatcher import Dispatcher
from omo_task_queue.state import StateMachine, Task, TaskStatus
from omo_task_queue.store import Config, Store

logger = logging.getLogger(__name__)


@dataclass
class RecoveryResult:
    recovered_running: list[Task] = field(default_factory=list)
    recovered_retry: list[Task] = field(default_factory=list)
    dispatched: list[Task] = field(default_factory=list)
    skipped: list[Task] = field(default_factory=list)


class RecoveryManager:
    def __init__(self, config: Optional[Config] = None, project_path: str = "") -> None:
        self.config = config or Config()
        self._recovery_in_progress: bool = False
        self._project_path = project_path

    @property
    def recovery_in_progress(self) -> bool:
        return self._recovery_in_progress

    def recover(self, store: Store, dispatcher: Dispatcher) -> RecoveryResult:
        if self._recovery_in_progress:
            raise RuntimeError("Recovery already in progress")

        self._recovery_in_progress = True
        result = RecoveryResult()

        try:
            running_tasks = store.list_tasks(
                status=TaskStatus.RUNNING,
                project_path=self._project_path,
            )
            retry_tasks = store.list_tasks(
                status=TaskStatus.RETRY_WAIT,
                project_path=self._project_path,
            )

            for task in running_tasks:
                recovered = self._recover_running_task(task, store)
                result.recovered_running.append(recovered)

            for task in retry_tasks:
                action = self._recover_retry_task(task, store, dispatcher)
                if action == "dispatched":
                    result.dispatched.append(task)
                elif action == "skipped":
                    result.skipped.append(task)
                else:
                    result.recovered_retry.append(task)

            logger.info(
                "Recovery complete: %d running→retry_wait, %d retry dispatched, "
                "%d retry skipped, %d retry kept",
                len(result.recovered_running),
                len(result.dispatched),
                len(result.skipped),
                len(result.recovered_retry),
            )
        finally:
            self._recovery_in_progress = False

        return result

    def _recover_running_task(self, task: Task, store: Store) -> Task:
        logger.info(
            "Task %s was RUNNING at shutdown — treating as interrupted", task.id
        )
        StateMachine.transition(task, TaskStatus.RETRY_WAIT)
        task.error_message = task.error_message or "Interrupted by shutdown"
        store.update_task(task)
        return task

    def _recover_retry_task(
        self, task: Task, store: Store, dispatcher: Dispatcher
    ) -> str:
        if task.retry_count < task.max_retries:
            logger.info(
                "Task %s in RETRY_WAIT with %d/%d retries — re-dispatching",
                task.id,
                task.retry_count,
                task.max_retries,
            )
            StateMachine.transition(task, TaskStatus.RUNNING)
            store.update_task(task)
            dispatcher.dispatch(task)
            return "dispatched"

        logger.warning(
            "Task %s in RETRY_WAIT but max retries (%d) exceeded — "
            "manual intervention required",
            task.id,
            task.max_retries,
        )
        return "skipped"
