"""OMO Task Queue Plugin — Retry-in-place policy and backoff controls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from omo_task_queue.state import StateMachine, Task, TaskStatus
from omo_task_queue.store import Config

logger = logging.getLogger(__name__)


@dataclass
class RetryResult:
    """Outcome of a retry decision."""

    should_retry: bool
    backoff_seconds: int
    next_retry_at: Optional[datetime] = None
    reason: str = ""


class RetryManager:
    """Handles failure/interruption recovery with configurable retry count and backoff.

    On failure: increments retry_count, transitions to RETRY_WAIT, and schedules
    a retry after backoff.  After max_retries is exceeded the task remains in
    RETRY_WAIT and requires manual intervention (skip/done).

    The queue never advances to the next task while a retry is pending.
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()

    def handle_failure(
        self, task: Task, error: Optional[Exception] = None
    ) -> RetryResult:
        """Process a task failure.

        Increments the task's retry_count, records the error message, transitions
        the task to RETRY_WAIT via the StateMachine, and returns a RetryResult
        indicating whether a retry should be scheduled.

        Args:
            task: The task that failed.  Mutated in-place.
            error: Optional exception that caused the failure.

        Returns:
            RetryResult with should_retry, backoff timing, and reason.
        """
        task.retry_count += 1
        if error is not None:
            task.error_message = str(error)
        else:
            task.error_message = "Unknown error"

        if task.status is not TaskStatus.RETRY_WAIT:
            StateMachine.transition(task, TaskStatus.RETRY_WAIT)

        if self.should_retry(task):
            backoff = self._calculate_backoff(task)
            next_retry = datetime.utcnow() + timedelta(seconds=backoff)
            logger.info(
                "Task %s failed (retry %d/%d). Next retry at %s (backoff %ds).",
                task.id,
                task.retry_count,
                self.config.max_retries,
                next_retry.isoformat(),
                backoff,
            )
            return RetryResult(
                should_retry=True,
                backoff_seconds=backoff,
                next_retry_at=next_retry,
                reason=f"Retry {task.retry_count}/{self.config.max_retries} scheduled",
            )

        logger.warning(
            "Task %s exceeded max retries (%d). Manual intervention required.",
            task.id,
            self.config.max_retries,
        )
        return RetryResult(
            should_retry=False,
            backoff_seconds=0,
            reason=f"Max retries ({self.config.max_retries}) exceeded — manual intervention required",
        )

    def should_retry(self, task: Task) -> bool:
        """Return True if the task has remaining retry attempts."""
        return task.retry_count < self.config.max_retries

    def _calculate_backoff(self, task: Task) -> int:
        """Calculate the backoff delay in seconds for a task.

        Uses exponential backoff: base * (2 ** retry_count), capped at 300s.
        """
        base = self.config.retry_backoff_seconds
        backoff = base * (2 ** (task.retry_count - 1))
        return min(backoff, 300)

    def schedule_retry(self, task: Task) -> datetime:
        """Transition a task from RETRY_WAIT back to RUNNING.

        This is called when the backoff timer expires and the task should be
        retried.  The caller is responsible for ensuring the backoff period
        has elapsed.

        Args:
            task: The task to retry.  Must be in RETRY_WAIT status.

        Returns:
            The UTC datetime when the retry was scheduled.
        """
        if task.status is not TaskStatus.RETRY_WAIT:
            raise ValueError(
                f"Cannot schedule retry for task in status {task.status.value}"
            )
        StateMachine.transition(task, TaskStatus.RUNNING)
        now = datetime.utcnow()
        logger.info("Task %s retry scheduled at %s", task.id, now.isoformat())
        return now

    def next_retry_at(self, task: Task) -> Optional[datetime]:
        if task.status is not TaskStatus.RETRY_WAIT or task.retry_count <= 0:
            return None
        return task.updated_at + timedelta(seconds=self._calculate_backoff(task))
