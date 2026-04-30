"""Tests for the retry manager and backoff controls."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from omo_task_queue.retry import RetryManager, RetryResult
from omo_task_queue.state import ExecutionMode, StateMachine, Task, TaskStatus
from omo_task_queue.store import Config


def _make_running_task(max_retries: int = 3, retry_count: int = 0) -> Task:
    return Task(
        id="task-1",
        title="Test Task",
        prompt="Do something",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.RUNNING,
        max_retries=max_retries,
        retry_count=retry_count,
    )


class TestRetryResult:
    def test_dataclass_fields(self) -> None:
        result = RetryResult(
            should_retry=True,
            backoff_seconds=5,
            next_retry_at=datetime.utcnow(),
            reason="test",
        )
        assert result.should_retry is True
        assert result.backoff_seconds == 5
        assert result.next_retry_at is not None
        assert result.reason == "test"


class TestRetryManagerDefaults:
    def test_uses_default_config(self) -> None:
        mgr = RetryManager()
        assert mgr.config.max_retries == 3
        assert mgr.config.retry_backoff_seconds == 5

    def test_uses_custom_config(self) -> None:
        cfg = Config(max_retries=5, retry_backoff_seconds=10)
        mgr = RetryManager(config=cfg)
        assert mgr.config.max_retries == 5
        assert mgr.config.retry_backoff_seconds == 10


class TestHandleFailureIncrementsCount:
    def test_increments_retry_count(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        mgr.handle_failure(task)
        assert task.retry_count == 1

    def test_increments_multiple_times(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        for expected in range(1, 4):
            mgr.handle_failure(task)
            assert task.retry_count == expected

    def test_records_error_message(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        exc = RuntimeError("Something broke")
        mgr.handle_failure(task, error=exc)
        assert task.error_message == "Something broke"

    def test_records_unknown_error_when_none(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        mgr.handle_failure(task)
        assert task.error_message == "Unknown error"


class TestHandleFailureTransitions:
    def test_transitions_running_to_retry_wait(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        mgr.handle_failure(task)
        assert task.status == TaskStatus.RETRY_WAIT

    def test_updates_updated_at(self) -> None:
        task = _make_running_task()
        before = task.updated_at
        mgr = RetryManager()
        mgr.handle_failure(task)
        assert task.updated_at > before

    def test_does_not_set_completed_at(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        mgr.handle_failure(task)
        assert task.completed_at is None


class TestShouldRetry:
    def test_true_when_under_max(self) -> None:
        task = _make_running_task(max_retries=3, retry_count=0)
        mgr = RetryManager()
        assert mgr.should_retry(task) is True

    def test_false_when_at_max(self) -> None:
        task = _make_running_task(max_retries=3, retry_count=3)
        mgr = RetryManager()
        assert mgr.should_retry(task) is False

    def test_false_when_over_max(self) -> None:
        task = _make_running_task(max_retries=3, retry_count=4)
        mgr = RetryManager()
        assert mgr.should_retry(task) is False

    def test_true_for_zero_retries(self) -> None:
        task = _make_running_task(max_retries=1, retry_count=0)
        mgr = RetryManager()
        assert mgr.should_retry(task) is True

    def test_false_for_one_retry_with_max_one(self) -> None:
        task = _make_running_task(max_retries=1, retry_count=1)
        mgr = RetryManager(config=Config(max_retries=1))
        assert mgr.should_retry(task) is False


class TestHandleFailureResult:
    def test_returns_should_retry_true_when_under_max(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        result = mgr.handle_failure(task)
        assert result.should_retry is True

    def test_returns_should_retry_false_when_max_exceeded(self) -> None:
        task = _make_running_task(max_retries=2, retry_count=2)
        mgr = RetryManager()
        result = mgr.handle_failure(task)
        assert result.should_retry is False

    def test_backoff_seconds_in_result(self) -> None:
        task = _make_running_task()
        mgr = RetryManager(Config(retry_backoff_seconds=5))
        result = mgr.handle_failure(task)
        assert result.backoff_seconds == 5

    def test_next_retry_at_is_future(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        before = datetime.utcnow()
        result = mgr.handle_failure(task)
        assert result.next_retry_at is not None
        assert result.next_retry_at >= before + timedelta(
            seconds=result.backoff_seconds
        )

    def test_reason_contains_retry_count(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        result = mgr.handle_failure(task)
        assert "Retry 1/3" in result.reason

    def test_reason_when_max_exceeded(self) -> None:
        task = _make_running_task(max_retries=1, retry_count=1)
        mgr = RetryManager(config=Config(max_retries=1))
        result = mgr.handle_failure(task)
        assert "Max retries" in result.reason
        assert "manual intervention" in result.reason


class TestBackoffCalculation:
    def test_first_retry_uses_base(self) -> None:
        task = _make_running_task(retry_count=1)
        mgr = RetryManager(Config(retry_backoff_seconds=5))
        assert mgr._calculate_backoff(task) == 5

    def test_second_retry_doubles(self) -> None:
        task = _make_running_task(retry_count=2)
        mgr = RetryManager(Config(retry_backoff_seconds=5))
        assert mgr._calculate_backoff(task) == 10

    def test_third_retry_quadruples(self) -> None:
        task = _make_running_task(retry_count=3)
        mgr = RetryManager(Config(retry_backoff_seconds=5))
        assert mgr._calculate_backoff(task) == 20

    def test_backoff_capped_at_300(self) -> None:
        task = _make_running_task(retry_count=10)
        mgr = RetryManager(Config(retry_backoff_seconds=60))
        assert mgr._calculate_backoff(task) == 300

    def test_custom_base(self) -> None:
        task = _make_running_task(retry_count=2)
        mgr = RetryManager(Config(retry_backoff_seconds=10))
        assert mgr._calculate_backoff(task) == 20


class TestMaxRetriesExceeded:
    def test_stays_in_retry_wait(self) -> None:
        task = _make_running_task(max_retries=1, retry_count=1)
        mgr = RetryManager()
        mgr.handle_failure(task)
        assert task.status == TaskStatus.RETRY_WAIT

    def test_does_not_transition_to_running(self) -> None:
        task = _make_running_task(max_retries=1, retry_count=1)
        mgr = RetryManager(config=Config(max_retries=1))
        result = mgr.handle_failure(task)
        assert result.should_retry is False

    def test_requires_manual_intervention(self) -> None:
        task = _make_running_task(max_retries=1, retry_count=1)
        mgr = RetryManager(config=Config(max_retries=1))
        result = mgr.handle_failure(task)
        assert "manual intervention" in result.reason


class TestNoQueueAdvancement:
    def test_failure_leaves_task_in_retry_wait(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        mgr.handle_failure(task)
        assert task.status == TaskStatus.RETRY_WAIT

    def test_retry_wait_is_not_terminal(self) -> None:
        task = _make_running_task()
        mgr = RetryManager()
        mgr.handle_failure(task)
        assert task.is_terminal() is False


class TestScheduleRetry:
    def test_transitions_retry_wait_to_running(self) -> None:
        task = _make_running_task()
        StateMachine.transition(task, TaskStatus.RETRY_WAIT)
        mgr = RetryManager()
        mgr.schedule_retry(task)
        assert task.status == TaskStatus.RUNNING

    def test_updates_updated_at(self) -> None:
        task = _make_running_task()
        StateMachine.transition(task, TaskStatus.RETRY_WAIT)
        before = task.updated_at
        mgr = RetryManager()
        mgr.schedule_retry(task)
        assert task.updated_at > before

    def test_raises_if_not_retry_wait(self) -> None:
        task = Task(
            id="task-1",
            title="Test Task",
            prompt="Do something",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
        )
        mgr = RetryManager()
        with pytest.raises(ValueError, match="Cannot schedule retry"):
            mgr.schedule_retry(task)

    def test_returns_datetime(self) -> None:
        task = _make_running_task()
        StateMachine.transition(task, TaskStatus.RETRY_WAIT)
        mgr = RetryManager()
        result = mgr.schedule_retry(task)
        assert isinstance(result, datetime)


class TestIntegrationWithStateMachine:
    def test_full_retry_cycle(self) -> None:
        task = _make_running_task(max_retries=3)
        mgr = RetryManager()

        first = mgr.handle_failure(task)
        assert task.status == TaskStatus.RETRY_WAIT
        assert task.retry_count == 1
        assert first.should_retry is True

        mgr.schedule_retry(task)
        assert task.status == TaskStatus.RUNNING

        second = mgr.handle_failure(task)
        assert task.status == TaskStatus.RETRY_WAIT
        assert task.retry_count == 2
        assert second.should_retry is True

        mgr.schedule_retry(task)
        assert task.status == TaskStatus.RUNNING

        third = mgr.handle_failure(task)
        assert task.status == TaskStatus.RETRY_WAIT
        assert task.retry_count == 3
        assert third.should_retry is False

        StateMachine.transition(task, TaskStatus.DONE)
        assert task.status == TaskStatus.DONE
        assert task.is_terminal() is True

    def test_manual_skip_from_retry_wait(self) -> None:
        task = _make_running_task(max_retries=1)
        mgr = RetryManager()
        mgr.handle_failure(task)
        assert task.status == TaskStatus.RETRY_WAIT
        StateMachine.transition(task, TaskStatus.SKIPPED)
        assert task.status == TaskStatus.SKIPPED
        assert task.is_terminal() is True


class TestRetryWaitToRunning:
    def test_state_machine_allows_retry_wait_to_running(self) -> None:
        task = Task(
            id="t1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RETRY_WAIT,
        )
        assert (
            StateMachine.can_transition(TaskStatus.RETRY_WAIT, TaskStatus.RUNNING)
            is True
        )
        StateMachine.transition(task, TaskStatus.RUNNING)
        assert task.status == TaskStatus.RUNNING

    def test_retry_wait_to_done(self) -> None:
        task = Task(
            id="t1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RETRY_WAIT,
        )
        StateMachine.transition(task, TaskStatus.DONE)
        assert task.status == TaskStatus.DONE

    def test_retry_wait_to_skipped(self) -> None:
        task = Task(
            id="t1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RETRY_WAIT,
        )
        StateMachine.transition(task, TaskStatus.SKIPPED)
        assert task.status == TaskStatus.SKIPPED
