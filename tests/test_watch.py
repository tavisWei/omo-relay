from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from omo_task_queue.session_continuer import ContinuationState, ContinuationStateStore
from omo_task_queue.state import ExecutionMode, Task, TaskStatus
from omo_task_queue.store import Config, SQLiteStore
from omo_task_queue.tmux_target import TmuxTarget
from omo_task_queue.tmux_target import TmuxTargetStore
from omo_task_queue.watch import WatchLoop
from omo_task_queue.watcher_status import WatcherStatusStore


class _ReadySnapshot:
    latest_message_id = "msg-1"
    latest_message_role = "assistant"
    latest_message_completed_ms = 1
    latest_activity_ms = 1
    root_session_id = "ses-1"

    def ready_for_continuation(self, idle_threshold: int) -> bool:
        return True

    def soft_stalled(self, idle_threshold: int, soft_stalled_threshold: int) -> bool:
        return False

    def stalled(self, stalled_threshold: int) -> bool:
        return False

    def is_quiet(self, idle_threshold: int) -> bool:
        return True


class _RecoveredWithoutAdvanceSnapshot(_ReadySnapshot):
    latest_message_id = "msg-1"
    latest_message_completed_ms = 2_000
    latest_activity_ms = 2_000


class _NotReadySnapshot(_ReadySnapshot):
    def ready_for_continuation(self, idle_threshold: int) -> bool:
        return False

    def is_quiet(self, idle_threshold: int) -> bool:
        return False


class _Observer:
    def locate_primary_session(self) -> str:
        return "ses-1"

    def snapshot(self, session_id: str) -> _ReadySnapshot:
        return _ReadySnapshot()


class _NotReadyObserver(_Observer):
    def snapshot(self, session_id: str) -> _NotReadySnapshot:
        return _NotReadySnapshot()


class _FailingContinuer:
    def continue_task(
        self, session_id: str, task: Task
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["tmux"],
            1,
            stdout="",
            stderr="tmux pane command mismatch: node",
        )


class _SuccessContinuer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def continue_task(
        self, session_id: str, task: Task
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((session_id, task.id))
        return subprocess.CompletedProcess(["tmux"], 0, stdout="", stderr="")


class _EnsureOnlyContinuer(_SuccessContinuer):
    def __init__(self) -> None:
        super().__init__()
        self.ensure_calls: list[tuple[str, str]] = []

    def ensure_task_target(self, session_id: str, task: Task):
        self.ensure_calls.append((session_id, task.id))
        return TmuxTarget(
            session_name="omo-test",
            pane_id="%1",
            attach_command="tmux attach -t omo-test",
            project_dir=task.project_path,
            opencode_session_id=task.target_session_id or session_id,
        )


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[Task, Task | None]] = []

    def send_queue_completion_notification(self, completed_task: Task, next_task):
        self.calls.append((completed_task, next_task))


def test_watch_loop_persists_actual_launch_error(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.PENDING,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    store.add_task(task)

    loop = WatchLoop(
        store=store,
        config=Config(),
        observer=_Observer(),
        continuer=_FailingContinuer(),
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.RETRY_WAIT
    assert updated.error_message == "tmux pane command mismatch: node"

    snapshot = WatcherStatusStore(tmp_path / ".omo_watcher_status.json").load()
    assert snapshot is not None
    assert snapshot.last_error == "tmux pane command mismatch: node"

    store.close()


def test_watch_loop_tmux_pane_not_ready_does_not_increment_retry_budget(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.PENDING,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    store.add_task(task)

    class _PaneNotReadyContinuer:
        def continue_task(self, session_id: str, task: Task):
            return subprocess.CompletedProcess(
                ["tmux"], 1, stdout="", stderr="tmux pane not ready"
            )

    loop = WatchLoop(
        store=store,
        config=Config(),
        observer=_Observer(),
        continuer=_PaneNotReadyContinuer(),
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.RETRY_WAIT
    assert updated.retry_count == 0
    assert updated.error_message == "tmux pane not ready"

    store.close()


def test_watch_loop_requeues_running_task_when_session_did_not_advance(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.RUNNING,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    store.add_task(task)
    state_store = ContinuationStateStore(tmp_path / ".omo_session_watch_state.json")
    state_store.save(
        ContinuationState(
            task_id="t1",
            session_id="ses-1",
            baseline_message_id="msg-1",
            launched_at_ms=1_000,
        )
    )

    class _ObserverNoAdvance(_Observer):
        def snapshot(self, session_id: str) -> _RecoveredWithoutAdvanceSnapshot:
            return _RecoveredWithoutAdvanceSnapshot()

    continuer = _SuccessContinuer()

    loop = WatchLoop(
        store=store,
        config=Config(),
        observer=_ObserverNoAdvance(),
        continuer=continuer,
        state_store=state_store,
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.RUNNING
    assert updated.error_message == "Continuation did not advance session"
    assert continuer.calls == [("ses-1", "t1")]

    state = state_store.load()
    assert state is not None
    assert state.task_id == "t1"

    snapshot = WatcherStatusStore(tmp_path / ".omo_watcher_status.json").load()
    assert snapshot is not None
    assert snapshot.decision == "launch_success"
    assert snapshot.reason == "continuation_sent"


def test_watch_loop_redispatches_due_retry_wait_task(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.RETRY_WAIT,
        retry_count=1,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    task.updated_at = datetime.utcnow() - timedelta(seconds=10)
    store.add_task(task)
    continuer = _SuccessContinuer()

    loop = WatchLoop(
        store=store,
        config=Config(retry_backoff_seconds=5),
        observer=_Observer(),
        continuer=continuer,
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.RUNNING
    assert continuer.calls == [("ses-1", "t1")]

    state = ContinuationStateStore(tmp_path / ".omo_session_watch_state.json").load()
    assert state is not None
    assert state.task_id == "t1"

    store.close()


def test_watch_loop_launch_success_keeps_pending_task_running(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.PENDING,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    store.add_task(task)
    continuer = _SuccessContinuer()

    loop = WatchLoop(
        store=store,
        config=Config(),
        observer=_Observer(),
        continuer=continuer,
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.RUNNING
    assert continuer.calls == [("ses-1", "t1")]

    snapshot = WatcherStatusStore(tmp_path / ".omo_watcher_status.json").load()
    assert snapshot is not None
    assert snapshot.decision == "launch_success"
    assert snapshot.running_task_id == "t1"
    assert snapshot.active_continuation_task_id == "t1"

    state = ContinuationStateStore(tmp_path / ".omo_session_watch_state.json").load()
    assert state is not None
    assert state.task_id == "t1"

    store.close()


def test_watch_loop_does_not_redispatch_retry_wait_when_not_ready(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.RETRY_WAIT,
        retry_count=1,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    task.updated_at = datetime.utcnow() - timedelta(seconds=10)
    store.add_task(task)
    continuer = _SuccessContinuer()

    loop = WatchLoop(
        store=store,
        config=Config(retry_backoff_seconds=5),
        observer=_NotReadyObserver(),
        continuer=continuer,
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.RETRY_WAIT
    assert continuer.calls == []

    store.close()


def test_watch_loop_precreates_target_while_not_ready_for_pending_task(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.PENDING,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    store.add_task(task)
    continuer = _EnsureOnlyContinuer()

    loop = WatchLoop(
        store=store,
        config=Config(),
        observer=_NotReadyObserver(),
        continuer=continuer,
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.PENDING
    assert continuer.ensure_calls == [("ses-1", "t1")]
    assert continuer.calls == []

    store.close()


def test_watch_loop_precreates_target_while_not_ready_for_retry_wait_task(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.RETRY_WAIT,
        retry_count=1,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    task.updated_at = datetime.utcnow() - timedelta(seconds=10)
    store.add_task(task)
    continuer = _EnsureOnlyContinuer()

    loop = WatchLoop(
        store=store,
        config=Config(retry_backoff_seconds=5),
        observer=_NotReadyObserver(),
        continuer=continuer,
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.RETRY_WAIT
    assert continuer.ensure_calls == [("ses-1", "t1")]
    assert continuer.calls == []

    store.close()


def test_watch_loop_precreates_target_for_tmux_recovery_task_at_max_retries(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.RETRY_WAIT,
        retry_count=3,
        max_retries=3,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
        error_message="tmux pane not ready",
    )
    task.updated_at = datetime.utcnow() - timedelta(seconds=10)
    store.add_task(task)
    continuer = _EnsureOnlyContinuer()

    loop = WatchLoop(
        store=store,
        config=Config(retry_backoff_seconds=5),
        observer=_NotReadyObserver(),
        continuer=continuer,
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.RETRY_WAIT
    assert updated.retry_count == 3
    assert continuer.ensure_calls == [("ses-1", "t1")]
    assert continuer.calls == []

    store.close()


def test_mark_task_done_notifies_next_task(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    running = Task(
        id="done-me",
        title="Done Task",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.RUNNING,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    pending = Task(
        id="next-one",
        title="Next Task",
        prompt="next",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.PENDING,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    store.add_task(running)
    store.add_task(pending)
    notifier = _RecordingNotifier()

    loop = WatchLoop(
        store=store,
        config=Config(),
        observer=_Observer(),
        continuer=_SuccessContinuer(),
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        notifier=notifier,
        poll_interval_seconds=0,
    )

    loop._mark_task_done("done-me")

    updated = store.get_task("done-me", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.DONE
    assert len(notifier.calls) == 1
    completed_task, next_task = notifier.calls[0]
    assert completed_task.title == "Done Task"
    assert next_task is not None
    assert next_task.title == "Next Task"

    store.close()


def test_mark_task_done_notifies_all_done_when_no_next_task(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    running = Task(
        id="done-me",
        title="Done Task",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.RUNNING,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    store.add_task(running)
    notifier = _RecordingNotifier()

    loop = WatchLoop(
        store=store,
        config=Config(),
        observer=_Observer(),
        continuer=_SuccessContinuer(),
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        notifier=notifier,
        poll_interval_seconds=0,
    )

    loop._mark_task_done("done-me")

    updated = store.get_task("done-me", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.DONE
    assert len(notifier.calls) == 1
    completed_task, next_task = notifier.calls[0]
    assert completed_task.title == "Done Task"
    assert next_task is None

    store.close()


def test_watch_loop_does_not_redispatch_retry_wait_at_max_retries(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    task = Task(
        id="t1",
        title="Task t1",
        prompt="hello",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.RETRY_WAIT,
        retry_count=3,
        max_retries=3,
        project_path=str(tmp_path.resolve()),
        target_session_id="ses-1",
    )
    task.updated_at = datetime.utcnow() - timedelta(seconds=60)
    store.add_task(task)
    continuer = _SuccessContinuer()

    loop = WatchLoop(
        store=store,
        config=Config(retry_backoff_seconds=5),
        observer=_Observer(),
        continuer=continuer,
        state_store=ContinuationStateStore(tmp_path / ".omo_session_watch_state.json"),
        watcher_status_store=WatcherStatusStore(tmp_path / ".omo_watcher_status.json"),
        tmux_target_store=TmuxTargetStore(tmp_path / ".omo_tmux_target.json"),
        project_path=str(tmp_path.resolve()),
        poll_interval_seconds=0,
    )

    loop.run_once()

    updated = store.get_task("t1", project_path=str(tmp_path.resolve()))
    assert updated is not None
    assert updated.status == TaskStatus.RETRY_WAIT
    assert updated.retry_count == 3
    assert continuer.calls == []

    store.close()
