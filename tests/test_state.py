from __future__ import annotations

from datetime import datetime

import pytest

from omo_task_queue.state import ExecutionMode, StateMachine, Task, TaskStatus


class TestTaskStatus:
    def test_enum_values(self) -> None:
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.RETRY_WAIT.value == "retry_wait"
        assert TaskStatus.DONE.value == "done"
        assert TaskStatus.SKIPPED.value == "skipped"


class TestExecutionMode:
    def test_enum_values(self) -> None:
        assert ExecutionMode.ONE_SHOT.value == "one_shot"
        assert ExecutionMode.ULW_LOOP.value == "ulw_loop"
        assert ExecutionMode.RALPH_LOOP.value == "ralph_loop"


class TestTask:
    def test_defaults(self) -> None:
        task = Task(id="t1", title="Test", prompt="Do it", mode=ExecutionMode.ONE_SHOT)
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 0
        assert task.max_retries == 3
        assert task.completed_at is None
        assert task.error_message is None
        assert task.order == 0
        assert isinstance(task.created_at, datetime)
        assert isinstance(task.updated_at, datetime)

    def test_is_terminal_done(self) -> None:
        task = Task(
            id="t1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.DONE,
        )
        assert task.is_terminal() is True

    def test_is_terminal_skipped(self) -> None:
        task = Task(
            id="t1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.SKIPPED,
        )
        assert task.is_terminal() is True

    def test_is_terminal_pending(self) -> None:
        task = Task(
            id="t1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
        )
        assert task.is_terminal() is False


class TestStateMachineCanTransition:
    def test_pending_to_running(self) -> None:
        assert (
            StateMachine.can_transition(TaskStatus.PENDING, TaskStatus.RUNNING) is True
        )

    def test_running_to_done(self) -> None:
        assert StateMachine.can_transition(TaskStatus.RUNNING, TaskStatus.DONE) is True

    def test_running_to_retry_wait(self) -> None:
        assert (
            StateMachine.can_transition(TaskStatus.RUNNING, TaskStatus.RETRY_WAIT)
            is True
        )

    def test_retry_wait_to_running(self) -> None:
        assert (
            StateMachine.can_transition(TaskStatus.RETRY_WAIT, TaskStatus.RUNNING)
            is True
        )

    def test_retry_wait_to_skipped(self) -> None:
        assert (
            StateMachine.can_transition(TaskStatus.RETRY_WAIT, TaskStatus.SKIPPED)
            is True
        )

    def test_retry_wait_to_done(self) -> None:
        assert (
            StateMachine.can_transition(TaskStatus.RETRY_WAIT, TaskStatus.DONE) is True
        )

    def test_done_is_terminal(self) -> None:
        for target in TaskStatus:
            assert StateMachine.can_transition(TaskStatus.DONE, target) is False

    def test_skipped_is_terminal(self) -> None:
        for target in TaskStatus:
            assert StateMachine.can_transition(TaskStatus.SKIPPED, target) is False

    def test_pending_to_done_is_illegal(self) -> None:
        assert StateMachine.can_transition(TaskStatus.PENDING, TaskStatus.DONE) is False

    def test_running_to_pending_is_illegal(self) -> None:
        assert (
            StateMachine.can_transition(TaskStatus.RUNNING, TaskStatus.PENDING) is False
        )


class TestStateMachineTransition:
    def test_pending_to_running(self) -> None:
        task = Task(id="t1", title="Test", prompt="Do it", mode=ExecutionMode.ONE_SHOT)
        StateMachine.transition(task, TaskStatus.RUNNING)
        assert task.status == TaskStatus.RUNNING
        assert task.updated_at >= task.created_at

    def test_running_to_done_sets_completed_at(self) -> None:
        task = Task(id="t1", title="Test", prompt="Do it", mode=ExecutionMode.ONE_SHOT)
        StateMachine.transition(task, TaskStatus.RUNNING)
        StateMachine.transition(task, TaskStatus.DONE)
        assert task.status == TaskStatus.DONE
        assert task.completed_at is not None
        assert task.completed_at == task.updated_at

    def test_running_to_retry_wait(self) -> None:
        task = Task(id="t1", title="Test", prompt="Do it", mode=ExecutionMode.ONE_SHOT)
        StateMachine.transition(task, TaskStatus.RUNNING)
        StateMachine.transition(task, TaskStatus.RETRY_WAIT)
        assert task.status == TaskStatus.RETRY_WAIT
        assert task.completed_at is None

    def test_retry_wait_to_running(self) -> None:
        task = Task(
            id="t1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RETRY_WAIT,
        )
        StateMachine.transition(task, TaskStatus.RUNNING)
        assert task.status == TaskStatus.RUNNING

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
        assert task.completed_at is not None

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
        assert task.completed_at is not None

    def test_illegal_transition_raises(self) -> None:
        task = Task(id="t1", title="Test", prompt="Do it", mode=ExecutionMode.ONE_SHOT)
        with pytest.raises(ValueError, match="Illegal transition"):
            StateMachine.transition(task, TaskStatus.DONE)

    def test_illegal_transition_message(self) -> None:
        task = Task(
            id="t1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.DONE,
        )
        with pytest.raises(ValueError, match="done → running"):
            StateMachine.transition(task, TaskStatus.RUNNING)

    def test_completed_at_not_overwritten(self) -> None:
        task = Task(
            id="t1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RETRY_WAIT,
            completed_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        first_completed = task.completed_at
        StateMachine.transition(task, TaskStatus.DONE)
        assert task.completed_at == first_completed
