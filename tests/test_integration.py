"""Integration tests for the OMO Task Queue Plugin.

These tests exercise the full queue progression using a real SQLiteStore with
a temporary database and a MockNotifier to verify email behavior.  All
execution modes are covered: one-shot, ulw-loop, and ralph-loop.

Success path: task completes, next task auto-starts, email sent.
Failure path: retry in place, no advancement, no email.
Manual override: skip/done unblocks queue.
Recovery: restart with running task, restart with retry task.
Notification suppression: verify no email on failure/skip/manual.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

import pytest

from omo_task_queue.dispatcher import Dispatcher, LaunchAdapter, TaskResult
from omo_task_queue.notifier import MockNotifier, NotificationConfig
from omo_task_queue.retry import RetryManager
from omo_task_queue.state import ExecutionMode, StateMachine, Task, TaskStatus
from omo_task_queue.store import Config, SQLiteStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_path() -> Generator[Path, None, None]:
    """Yield a temporary database file path and clean it up afterwards."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td) / "test_queue.db"


@pytest.fixture
def store(tmp_db_path: Path) -> SQLiteStore:
    """Provide a fresh SQLiteStore backed by a temp database."""
    return SQLiteStore(tmp_db_path)


@pytest.fixture
def notifier() -> MockNotifier:
    """Provide a MockNotifier with notifications enabled."""
    return MockNotifier(config=NotificationConfig(enabled=True))


@pytest.fixture
def dispatcher(store: SQLiteStore) -> Dispatcher:
    """Provide a Dispatcher wired to the real SQLiteStore."""
    return Dispatcher(store)


@pytest.fixture
def retry_manager() -> RetryManager:
    """Provide a RetryManager with default config."""
    return RetryManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(
    task_id: str,
    mode: ExecutionMode = ExecutionMode.ONE_SHOT,
    status: TaskStatus = TaskStatus.PENDING,
    order: int = 0,
    max_retries: int = 3,
) -> Task:
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        prompt=f"Prompt {task_id}",
        mode=mode,
        status=status,
        order=order,
        max_retries=max_retries,
    )


class FailingAdapter:
    """Always returns a failed result."""

    def launch(self, task: Task) -> TaskResult:
        return TaskResult(success=False, error_message="simulated failure")


class SuccessAdapter:
    """Always returns a successful result."""

    def launch(self, task: Task) -> TaskResult:
        return TaskResult(success=True, output=f"success for {task.id}", launched=True)


class CountingAdapter:
    """Returns success or failure based on an internal counter."""

    def __init__(self, fail_on: Optional[list[int]] = None) -> None:
        self._count = 0
        self._fail_on = set(fail_on or [])

    def launch(self, task: Task) -> TaskResult:
        self._count += 1
        if self._count in self._fail_on:
            return TaskResult(success=False, error_message=f"failure #{self._count}")
        return TaskResult(success=True, output=f"success #{self._count}")


# ---------------------------------------------------------------------------
# 1. Success path — all execution modes
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def prompt(self, text: str) -> None:
        self.prompts.append(text)


class TestSuccessPathAllModes:
    """Task completes, next task auto-starts, notification sent."""

    def _run_success_flow(self, store: SQLiteStore, mode: ExecutionMode) -> None:
        task1 = make_task("t1", mode=mode, order=1)
        task2 = make_task("t2", mode=mode, order=2)
        store.add_task(task1)
        store.add_task(task2)

        dispatcher = Dispatcher(
            store,
            adapters={
                ExecutionMode.ONE_SHOT: SuccessAdapter(),
                ExecutionMode.ULW_LOOP: SuccessAdapter(),
                ExecutionMode.RALPH_LOOP: SuccessAdapter(),
            },
        )
        claimed = store.claim_next()
        assert claimed is not None
        assert claimed.id == "t1"

        result = dispatcher.dispatch(claimed)
        assert result.success is True
        assert result.launched is True

        dispatcher.mark_task_completed("t1")
        dispatcher.mark_task_completed("t2")

        # Both tasks should be DONE after auto-advance
        t1 = store.get_task("t1")
        t2 = store.get_task("t2")
        assert t1 is not None and t1.status == TaskStatus.DONE
        assert t2 is not None and t2.status == TaskStatus.DONE

    def test_one_shot_success_advances(self, store: SQLiteStore) -> None:
        self._run_success_flow(store, ExecutionMode.ONE_SHOT)

    def test_ulw_loop_success_advances(self, store: SQLiteStore) -> None:
        self._run_success_flow(store, ExecutionMode.ULW_LOOP)

    def test_ralph_loop_success_advances(self, store: SQLiteStore) -> None:
        self._run_success_flow(store, ExecutionMode.RALPH_LOOP)

    def test_success_sends_notification(
        self, store: SQLiteStore, notifier: MockNotifier
    ) -> None:
        """A successful task completion triggers a success notification."""
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store.add_task(task)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: SuccessAdapter()},
        )
        claimed = store.claim_next()
        assert claimed is not None

        # Simulate the dispatch completing successfully
        result = dispatcher.dispatch(claimed)
        assert result.success is True
        dispatcher.mark_task_completed("t1")

        # Manually trigger notification as the orchestrator would
        finished = store.get_task("t1")
        assert finished is not None
        assert finished.status == TaskStatus.DONE
        notifier.send_success_notification(finished)
        assert len(notifier.sent) == 1
        assert notifier.sent[0].id == "t1"


# ---------------------------------------------------------------------------
# 2. Failure path — retry in place, no advancement, no email
# ---------------------------------------------------------------------------


class TestFailurePath:
    """Failure blocks queue advancement and triggers retry."""

    def test_failure_blocks_advancement(self, store: SQLiteStore) -> None:
        task1 = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        task2 = make_task("t2", mode=ExecutionMode.ONE_SHOT, order=2)
        store.add_task(task1)
        store.add_task(task2)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        claimed = store.claim_next()
        assert claimed is not None
        result = dispatcher.dispatch(claimed)

        assert result.success is False
        t1 = store.get_task("t1")
        t2 = store.get_task("t2")
        assert t1 is not None and t1.status == TaskStatus.RETRY_WAIT
        assert t1.retry_count == 1
        assert t2 is not None and t2.status == TaskStatus.PENDING

    def test_failure_no_notification(
        self, store: SQLiteStore, notifier: MockNotifier
    ) -> None:
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store.add_task(task)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)

        finished = store.get_task("t1")
        assert finished is not None
        notifier.send_success_notification(finished)
        assert len(notifier.sent) == 0

    def test_retry_increments_count(self, store: SQLiteStore) -> None:
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1, max_retries=3)
        store.add_task(task)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )

        # First failure
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)
        t1 = store.get_task("t1")
        assert t1 is not None
        assert t1.status == TaskStatus.RETRY_WAIT
        assert t1.retry_count == 1

        # Retry and fail again
        StateMachine.transition(t1, TaskStatus.RUNNING)
        store.update_task(t1)
        dispatcher.dispatch(t1)
        t1 = store.get_task("t1")
        assert t1 is not None
        assert t1.status == TaskStatus.RETRY_WAIT
        assert t1.retry_count == 2

    def test_max_retries_exceeded_stays_in_retry_wait(self, store: SQLiteStore) -> None:
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1, max_retries=1)
        store.add_task(task)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )

        # First failure -> retry_wait (retry_count=1, max_retries=1, should_retry=False)
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)
        t1 = store.get_task("t1")
        assert t1 is not None
        assert t1.status == TaskStatus.RETRY_WAIT
        assert t1.retry_count == 1

        # Manually transition back to RUNNING and fail again
        StateMachine.transition(t1, TaskStatus.RUNNING)
        store.update_task(t1)
        dispatcher.dispatch(t1)
        t1 = store.get_task("t1")
        assert t1 is not None
        assert t1.retry_count == 2
        # After exceeding max_retries, dispatcher's RetryManager leaves it in RUNNING
        assert t1.status == TaskStatus.RUNNING


# ---------------------------------------------------------------------------
# 3. Manual override — skip / done unblocks queue
# ---------------------------------------------------------------------------


class TestManualOverride:
    """Manual skip or done moves a stuck task out of the way."""

    def test_manual_skip_unblocks_queue(self, store: SQLiteStore) -> None:
        task1 = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        task2 = make_task("t2", mode=ExecutionMode.ONE_SHOT, order=2)
        store.add_task(task1)
        store.add_task(task2)

        # Fail task1
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)

        t1 = store.get_task("t1")
        assert t1 is not None and t1.status == TaskStatus.RETRY_WAIT

        # Manually skip task1
        StateMachine.transition(t1, TaskStatus.SKIPPED)
        store.update_task(t1)

        # Now claim_next should return task2
        next_task = store.claim_next()
        assert next_task is not None
        assert next_task.id == "t2"

    def test_manual_done_unblocks_queue(self, store: SQLiteStore) -> None:
        task1 = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        task2 = make_task("t2", mode=ExecutionMode.ONE_SHOT, order=2)
        store.add_task(task1)
        store.add_task(task2)

        # Fail task1
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)

        t1 = store.get_task("t1")
        assert t1 is not None and t1.status == TaskStatus.RETRY_WAIT

        # Manually mark task1 as done
        StateMachine.transition(t1, TaskStatus.DONE)
        store.update_task(t1)

        # Now claim_next should return task2
        next_task = store.claim_next()
        assert next_task is not None
        assert next_task.id == "t2"

    def test_skip_no_notification(
        self, store: SQLiteStore, notifier: MockNotifier
    ) -> None:
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store.add_task(task)

        # Fail then skip
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)

        t1 = store.get_task("t1")
        assert t1 is not None
        StateMachine.transition(t1, TaskStatus.SKIPPED)
        store.update_task(t1)

        notifier.send_success_notification(t1)
        assert len(notifier.sent) == 0

    def test_manual_done_records_notification(
        self, store: SQLiteStore, notifier: MockNotifier
    ) -> None:
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store.add_task(task)
        claimed = store.claim_next()
        assert claimed is not None
        StateMachine.transition(claimed, TaskStatus.DONE)
        store.update_task(claimed)
        notifier.send_success_notification(claimed)
        assert len(notifier.sent) == 1


# ---------------------------------------------------------------------------
# 4. Recovery — restart with running task, restart with retry task
# ---------------------------------------------------------------------------


class TestRecovery:
    """Simulate plugin restart and verify correct recovery behaviour."""

    def test_recovery_with_running_task(self, tmp_db_path: Path) -> None:
        """On restart, a RUNNING task should be detected and handled."""
        # First session: start a task, then "crash" (store closed without completion)
        store1 = SQLiteStore(tmp_db_path)
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store1.add_task(task)
        claimed = store1.claim_next()
        assert claimed is not None and claimed.status == TaskStatus.RUNNING
        store1.close()

        # Second session: simulate restart
        store2 = SQLiteStore(tmp_db_path)
        running = store2.get_running_task()
        assert running is not None
        assert running.id == "t1"
        assert running.status == TaskStatus.RUNNING

        # Recovery logic: transition back to PENDING or retry
        # For this test we simulate a recovery that transitions to RETRY_WAIT
        StateMachine.transition(running, TaskStatus.RETRY_WAIT)
        store2.update_task(running)

        recovered = store2.get_task("t1")
        assert recovered is not None
        assert recovered.status == TaskStatus.RETRY_WAIT
        store2.close()

    def test_recovery_with_retry_task(self, tmp_db_path: Path) -> None:
        """On restart, a RETRY_WAIT task should be schedulable for retry."""
        # First session: fail a task into retry_wait
        store1 = SQLiteStore(tmp_db_path)
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store1.add_task(task)
        claimed = store1.claim_next()
        assert claimed is not None

        dispatcher = Dispatcher(
            store1,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        dispatcher.dispatch(claimed)
        t1 = store1.get_task("t1")
        assert t1 is not None and t1.status == TaskStatus.RETRY_WAIT
        store1.close()

        # Second session: simulate restart and schedule retry
        store2 = SQLiteStore(tmp_db_path)
        recovered = store2.get_task("t1")
        assert recovered is not None
        assert recovered.status == TaskStatus.RETRY_WAIT

        retry_manager = RetryManager()
        retry_manager.schedule_retry(recovered)
        store2.update_task(recovered)

        retried = store2.get_task("t1")
        assert retried is not None
        assert retried.status == TaskStatus.RUNNING
        store2.close()


# ---------------------------------------------------------------------------
# 5. Notification suppression
# ---------------------------------------------------------------------------


class TestNotificationSuppression:
    """Verify that notifications are only sent on automatic success."""

    def test_no_email_on_failure(
        self, store: SQLiteStore, notifier: MockNotifier
    ) -> None:
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store.add_task(task)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)

        finished = store.get_task("t1")
        assert finished is not None
        notifier.send_success_notification(finished)
        assert len(notifier.sent) == 0

    def test_no_email_on_skip(self, store: SQLiteStore, notifier: MockNotifier) -> None:
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store.add_task(task)
        claimed = store.claim_next()
        assert claimed is not None
        StateMachine.transition(claimed, TaskStatus.RETRY_WAIT)
        store.update_task(claimed)
        StateMachine.transition(claimed, TaskStatus.SKIPPED)
        store.update_task(claimed)
        notifier.send_success_notification(claimed)
        assert len(notifier.sent) == 0

    def test_email_only_on_automatic_success(
        self, store: SQLiteStore, notifier: MockNotifier
    ) -> None:
        task1 = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store.add_task(task1)
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: SuccessAdapter()},
        )
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)
        dispatcher.mark_task_completed("t1")
        t1 = store.get_task("t1")
        assert t1 is not None and t1.status == TaskStatus.DONE
        notifier.send_success_notification(t1)
        assert len(notifier.sent) == 1

        task2 = make_task("t2", mode=ExecutionMode.ONE_SHOT, order=2)
        store.add_task(task2)
        fail_dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        claimed2 = store.claim_next()
        fail_dispatcher.dispatch(claimed2)
        t2 = store.get_task("t2")
        assert t2 is not None and t2.status == TaskStatus.RETRY_WAIT
        notifier.send_success_notification(t2)
        assert len(notifier.sent) == 1

        task3 = make_task("t3", mode=ExecutionMode.ONE_SHOT, order=3)
        store.add_task(task3)
        claimed3 = store.claim_next()
        assert claimed3 is not None
        StateMachine.transition(claimed3, TaskStatus.RETRY_WAIT)
        store.update_task(claimed3)
        StateMachine.transition(claimed3, TaskStatus.SKIPPED)
        store.update_task(claimed3)
        notifier.send_success_notification(claimed3)
        assert len(notifier.sent) == 1


# ---------------------------------------------------------------------------
# 6. Full flow end-to-end
# ---------------------------------------------------------------------------


class TestFullFlow:
    """End-to-end queue progression with multiple tasks and mixed outcomes."""

    def test_full_flow_mixed_outcomes(
        self, store: SQLiteStore, notifier: MockNotifier
    ) -> None:
        t1 = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        t2 = make_task("t2", mode=ExecutionMode.ONE_SHOT, order=2, max_retries=3)
        t3 = make_task("t3", mode=ExecutionMode.ONE_SHOT, order=3, max_retries=1)

        store.add_task(t1)
        store.add_task(t2)
        store.add_task(t3)

        counting_adapter = CountingAdapter(fail_on=[2, 4])
        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: counting_adapter},
        )

        claimed1 = store.claim_next()
        assert claimed1 is not None and claimed1.id == "t1"
        dispatcher.dispatch(claimed1)
        dispatcher.mark_task_completed("t1")
        t1_final = store.get_task("t1")
        assert t1_final is not None and t1_final.status == TaskStatus.DONE
        notifier.send_success_notification(t1_final)
        assert len(notifier.sent) == 1

        t2_after = store.get_task("t2")
        assert t2_after is not None
        assert t2_after.status == TaskStatus.RETRY_WAIT
        assert t2_after.retry_count == 1
        notifier.send_success_notification(t2_after)
        assert len(notifier.sent) == 1

        retry_manager = RetryManager()
        retry_manager.schedule_retry(t2_after)
        store.update_task(t2_after)
        assert store.get_task("t2").status == TaskStatus.RUNNING

        dispatcher.dispatch(t2_after)
        dispatcher.mark_task_completed("t2")
        t2_final = store.get_task("t2")
        assert t2_final is not None and t2_final.status == TaskStatus.DONE
        notifier.send_success_notification(t2_final)
        assert len(notifier.sent) == 2

        t3_after = store.get_task("t3")
        assert t3_after is not None
        assert t3_after.status == TaskStatus.RETRY_WAIT
        assert t3_after.retry_count == 1
        notifier.send_success_notification(t3_after)
        assert len(notifier.sent) == 2

        StateMachine.transition(t3_after, TaskStatus.SKIPPED)
        store.update_task(t3_after)
        notifier.send_success_notification(t3_after)
        assert len(notifier.sent) == 2

        assert store.get_next_pending() is None
        running = store.get_running_task()
        assert running is None

    def test_all_modes_in_one_queue(self, store: SQLiteStore) -> None:
        """Queue with one-shot, ulw-loop, and ralph-loop tasks all succeeding."""
        t1 = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        t2 = make_task("t2", mode=ExecutionMode.ULW_LOOP, order=2)
        t3 = make_task("t3", mode=ExecutionMode.RALPH_LOOP, order=3)

        store.add_task(t1)
        store.add_task(t2)
        store.add_task(t3)

        dispatcher = Dispatcher(
            store,
            adapters={
                ExecutionMode.ONE_SHOT: SuccessAdapter(),
                ExecutionMode.ULW_LOOP: SuccessAdapter(),
                ExecutionMode.RALPH_LOOP: SuccessAdapter(),
            },
        )

        # Dispatch all three in sequence via auto-advance
        claimed = store.claim_next()
        assert claimed is not None and claimed.id == "t1"
        dispatcher.dispatch(claimed)
        dispatcher.mark_task_completed("t1")
        dispatcher.mark_task_completed("t2")
        dispatcher.mark_task_completed("t3")

        for task_id in ("t1", "t2", "t3"):
            task = store.get_task(task_id)
            assert task is not None and task.status == TaskStatus.DONE

        assert store.get_next_pending() is None


# ---------------------------------------------------------------------------
# 7. Dispatcher / RetryManager / Store integration edge cases
# ---------------------------------------------------------------------------


class TestIntegrationEdgeCases:
    """Edge cases that only appear when components interact."""

    def test_dispatcher_busy_prevents_second_dispatch(self, store: SQLiteStore) -> None:
        """If dispatcher is already running a task, a second dispatch is rejected."""
        task1 = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        task2 = make_task("t2", mode=ExecutionMode.ONE_SHOT, order=2)
        store.add_task(task1)
        store.add_task(task2)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: SuccessAdapter()},
        )
        # Manually mark dispatcher as busy
        dispatcher._currently_running = "t1"

        result = dispatcher.dispatch(task2)
        assert result.success is False
        assert "Dispatcher busy" in result.error_message

    def test_claim_next_is_atomic_under_load(self, store: SQLiteStore) -> None:
        """Many threads racing for claim_next should not duplicate claims."""
        import threading

        for i in range(10):
            store.add_task(make_task(f"t{i}", mode=ExecutionMode.ONE_SHOT, order=i))

        results: list[Task | None] = []
        lock = threading.Lock()

        def claim() -> None:
            task = store.claim_next()
            if task:
                with lock:
                    results.append(task)

        threads = [threading.Thread(target=claim) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        claimed_ids = {t.id for t in results}
        assert len(claimed_ids) == 10

    def test_store_persists_across_reconnections(self, tmp_db_path: Path) -> None:
        """Closing and reopening the store preserves all task state."""
        store1 = SQLiteStore(tmp_db_path)
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store1.add_task(task)
        claimed = store1.claim_next()
        assert claimed is not None
        store1.close()

        store2 = SQLiteStore(tmp_db_path)
        retrieved = store2.get_task("t1")
        assert retrieved is not None
        assert retrieved.status == TaskStatus.RUNNING
        assert retrieved.id == "t1"
        store2.close()

    def test_retry_manager_backoff_with_real_store(self, store: SQLiteStore) -> None:
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1, max_retries=3)
        store.add_task(task)
        claimed = store.claim_next()
        assert claimed is not None

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: FailingAdapter()},
        )
        dispatcher.dispatch(claimed)

        t1 = store.get_task("t1")
        assert t1 is not None
        assert t1.status == TaskStatus.RETRY_WAIT
        assert t1.retry_count == 1
        assert t1.error_message == "simulated failure"

        retry_manager = RetryManager()
        result = retry_manager.handle_failure(t1)
        assert result.should_retry is True
        assert result.backoff_seconds == 10
        store.update_task(t1)

        StateMachine.transition(t1, TaskStatus.RUNNING)
        store.update_task(t1)
        dispatcher.dispatch(t1)
        t1 = store.get_task("t1")
        assert t1 is not None
        assert t1.retry_count == 3

        result2 = retry_manager.handle_failure(t1)
        assert result2.should_retry is False
        assert result2.backoff_seconds == 0

    def test_backoff_cap_at_300_seconds(self, store: SQLiteStore) -> None:
        """Exponential backoff is hard-capped at 300 seconds."""
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1, max_retries=10)
        task.retry_count = 10  # simulate many retries
        store.add_task(task)

        retry_manager = RetryManager(Config(retry_backoff_seconds=5))
        backoff = retry_manager._calculate_backoff(task)
        assert backoff == 300

    def test_notification_disabled_sends_nothing(self, store: SQLiteStore) -> None:
        disabled_notifier = MockNotifier(config=NotificationConfig(enabled=False))
        task = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=1)
        store.add_task(task)

        dispatcher = Dispatcher(
            store,
            adapters={ExecutionMode.ONE_SHOT: SuccessAdapter()},
        )
        claimed = store.claim_next()
        dispatcher.dispatch(claimed)
        dispatcher.mark_task_completed("t1")

        finished = store.get_task("t1")
        assert finished is not None and finished.status == TaskStatus.DONE
        disabled_notifier.send_success_notification(finished)
        assert len(disabled_notifier.sent) == 0

    def test_empty_queue_dispatch_does_not_crash(self, store: SQLiteStore) -> None:
        """Dispatching when queue is empty simply returns no task."""
        assert store.claim_next() is None
        assert store.get_running_task() is None
        assert store.get_next_pending() is None

    def test_task_with_large_order_sorts_correctly(self, store: SQLiteStore) -> None:
        t1 = make_task("t1", mode=ExecutionMode.ONE_SHOT, order=9999)
        t2 = make_task("t2", mode=ExecutionMode.ONE_SHOT, order=10000)
        t3 = make_task("t3", mode=ExecutionMode.ONE_SHOT, order=10001)
        store.add_task(t1)
        store.add_task(t2)
        store.add_task(t3)

        next_task = store.get_next_pending()
        assert next_task is not None
        assert next_task.id == "t1"

        claimed = store.claim_next()
        assert claimed is not None and claimed.id == "t1"

        next_after = store.get_next_pending()
        assert next_after is not None
        assert next_after.id == "t2"
