from __future__ import annotations

import argparse
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from omo_task_queue.logging_config import setup_logging
from omo_task_queue.notifier import EmailNotifier, MockNotifier, NotificationConfig
from omo_task_queue.opencode_observer import OpenCodeObserver
from omo_task_queue.retry import RetryManager
from omo_task_queue.session_continuer import (
    ContinuationState,
    ContinuationStateStore,
    OpencodeSessionContinuer,
)
from omo_task_queue.state import StateMachine, TaskStatus
from omo_task_queue.store import Config, SQLiteStore
from omo_task_queue.watcher_status import WatcherStatusSnapshot, WatcherStatusStore
from omo_task_queue.tmux_target import TmuxTargetStore

logger = logging.getLogger("omo_task_queue.watch")


class WatchLoop:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        config: Config,
        observer: OpenCodeObserver,
        continuer: OpencodeSessionContinuer,
        state_store: ContinuationStateStore,
        watcher_status_store: WatcherStatusStore,
        tmux_target_store: TmuxTargetStore,
        project_path: str,
        notifier=None,
        poll_interval_seconds: int = 5,
    ) -> None:
        self._store = store
        self._config = config
        self._observer = observer
        self._continuer = continuer
        self._state_store = state_store
        self._watcher_status_store = watcher_status_store
        self._tmux_target_store = tmux_target_store
        self._project_path = project_path
        self._notifier = notifier
        self._poll_interval_seconds = poll_interval_seconds
        self._retry_manager = RetryManager(config)

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self._poll_interval_seconds)

    def run_once(self) -> None:
        session_id = self._observer.locate_primary_session()
        if session_id is None:
            logger.info("No primary OpenCode session found for project yet")
            return

        snapshot = self._observer.snapshot(session_id)
        self._write_status(snapshot, decision="observed", reason="snapshot_loaded")
        logger.info(
            "watch.snapshot session=%s latest_message_id=%s latest_role=%s latest_completed_ms=%s latest_activity_ms=%s",
            session_id,
            snapshot.latest_message_id,
            snapshot.latest_message_role,
            snapshot.latest_message_completed_ms,
            snapshot.latest_activity_ms,
        )
        recovered = self._recover_running_task(snapshot)
        if recovered:
            return

        ready = snapshot.ready_for_continuation(self._config.idle_threshold)
        soft_stalled = snapshot.soft_stalled(
            self._config.idle_threshold,
            self._config.soft_stalled_threshold,
        )
        stalled = snapshot.stalled(self._config.stalled_threshold)
        self._write_status(
            snapshot,
            decision="eligible" if (ready or soft_stalled or stalled) else "waiting",
            reason="ready"
            if ready
            else (
                "soft_stalled"
                if soft_stalled
                else ("stalled" if stalled else "not_ready")
            ),
        )
        logger.info(
            "watch.decision session=%s ready=%s soft_stalled=%s stalled=%s idle_threshold=%s soft_stalled_threshold=%s stalled_threshold=%s",
            session_id,
            ready,
            soft_stalled,
            stalled,
            self._config.idle_threshold,
            self._config.soft_stalled_threshold,
            self._config.stalled_threshold,
        )
        self._ensure_target_for_actionable_task(session_id)
        if not ready and not soft_stalled and not stalled:
            logger.info(
                "watch.skip session=%s reason=not_ready role=%s completed_ms=%s soft_stalled=%s stalled=%s",
                session_id,
                snapshot.latest_message_role,
                snapshot.latest_message_completed_ms,
                soft_stalled,
                stalled,
            )
            return

        retried = self._retry_due_task(snapshot, session_id)
        if retried:
            return

        if self._store.get_running_task(project_path=self._project_path) is not None:
            self._write_status(
                snapshot, decision="waiting", reason="running_task_present"
            )
            logger.info("watch.skip session=%s reason=running_task_present", session_id)
            return

        next_task = self._store.claim_next(project_path=self._project_path)
        if next_task is None:
            self._write_status(snapshot, decision="waiting", reason="no_pending_task")
            logger.info("watch.skip session=%s reason=no_pending_task", session_id)
            return

        self._launch_task(snapshot, session_id, next_task)

    def _launch_task(self, snapshot, session_id: str, next_task) -> None:

        baseline_message_id = snapshot.latest_message_id
        launch_state = ContinuationState(
            task_id=next_task.id,
            session_id=session_id,
            baseline_message_id=baseline_message_id,
            launched_at_ms=int(time.time() * 1000),
        )
        self._state_store.save(launch_state)
        self._write_status(
            snapshot,
            decision="launching",
            reason="continuation_triggered",
            running_task_id=next_task.id,
            active_continuation_task_id=next_task.id,
            last_launch_task_id=next_task.id,
        )
        logger.info(
            "watch.launch task_id=%s title=%s session=%s baseline_message_id=%s mode=%s",
            next_task.id,
            next_task.title,
            session_id,
            baseline_message_id,
            next_task.mode.value,
        )
        result = self._continuer.continue_task(session_id, next_task)

        if result.returncode == 0:
            logger.info(
                "watch.launch_success task_id=%s session=%s returncode=%s",
                next_task.id,
                session_id,
                result.returncode,
            )
            self._write_status(
                snapshot,
                decision="launch_success",
                reason="continuation_sent",
                running_task_id=next_task.id,
                active_continuation_task_id=next_task.id,
                last_launch_task_id=next_task.id,
            )
            return

        logger.warning(
            "watch.launch_failed task_id=%s returncode=%s error=%s",
            next_task.id,
            result.returncode,
            result.stderr.strip() or result.stdout.strip() or "unknown error",
        )
        error_text = result.stderr.strip() or result.stdout.strip() or "unknown error"
        self._write_status(
            snapshot,
            decision="launch_failed",
            reason="continuation_failed",
            last_launch_task_id=next_task.id,
            last_error=error_text,
        )
        task = self._store.get_task(next_task.id, project_path=self._project_path)
        if self._is_tmux_recovery_error(error_text):
            self._tmux_target_store.clear()
            if task is not None:
                if task.status is not TaskStatus.RETRY_WAIT:
                    StateMachine.transition(task, TaskStatus.RETRY_WAIT)
                task.error_message = error_text
                task.updated_at = datetime.utcnow()
                self._store.update_task(task)
            self._state_store.clear()
            logger.info(
                "watch.tmux_recover_wait task_id=%s error=%s",
                next_task.id,
                error_text,
            )
            return
        if task is not None:
            retry_result = self._retry_manager.handle_failure(
                task, error=RuntimeError(error_text)
            )
            self._store.update_task(task)
            if not retry_result.should_retry:
                logger.warning("Task %s requires manual intervention", task.id)
        self._state_store.clear()

    def _ensure_target_for_actionable_task(self, session_id: str) -> None:
        task = self._next_actionable_task()
        if task is None:
            return
        ensure_target = getattr(self._continuer, "ensure_task_target", None)
        if ensure_target is None:
            return
        target_result = ensure_target(session_id, task)
        if isinstance(target_result, subprocess.CompletedProcess):
            logger.warning(
                "watch.ensure_target_failed task_id=%s returncode=%s error=%s",
                task.id,
                target_result.returncode,
                target_result.stderr.strip()
                or target_result.stdout.strip()
                or "unknown error",
            )

    def _next_actionable_task(self):
        retry_tasks = self._store.list_tasks(
            status=TaskStatus.RETRY_WAIT,
            project_path=self._project_path,
        )
        for task in retry_tasks:
            if self._is_tmux_recovery_task(task):
                return task
        for task in retry_tasks:
            if task.retry_count >= task.max_retries:
                continue
            return task
        return self._store.get_next_pending(project_path=self._project_path)

    def _retry_due_task(self, snapshot, session_id: str) -> bool:
        retry_tasks = self._store.list_tasks(
            status=TaskStatus.RETRY_WAIT,
            project_path=self._project_path,
        )
        due_task = None
        for task in retry_tasks:
            if self._is_tmux_recovery_task(task):
                due_task = task
                break
        if due_task is None:
            for task in retry_tasks:
                if task.retry_count >= task.max_retries:
                    continue
                next_retry_at = self._retry_manager.next_retry_at(task)
                if next_retry_at is None or next_retry_at <= datetime.utcnow():
                    due_task = task
                    break
        if due_task is None:
            return False

        self._retry_manager.schedule_retry(due_task)
        self._store.update_task(due_task)
        logger.info(
            "watch.retry_dispatch task_id=%s retry_count=%d",
            due_task.id,
            due_task.retry_count,
        )
        self._launch_task(snapshot, session_id, due_task)
        return True

    @staticmethod
    def _is_tmux_recovery_error(error_text: str | None) -> bool:
        if not error_text:
            return False
        return any(
            token in error_text
            for token in (
                "tmux pane not ready",
                "can't find pane",
                "can't find session",
                "tmux pane missing",
                "tmux session not running",
            )
        )

    def _is_tmux_recovery_task(self, task) -> bool:
        return self._is_tmux_recovery_error(task.error_message)

    def _recover_running_task(self, snapshot) -> bool:
        state = self._state_store.load()
        if state is None:
            return False

        logger.info(
            "watch.recover_check task_id=%s session=%s baseline_message_id=%s launched_at_ms=%s current_message_id=%s",
            state.task_id,
            state.session_id,
            state.baseline_message_id,
            state.launched_at_ms,
            snapshot.latest_message_id,
        )

        task = self._store.get_task(state.task_id, project_path=self._project_path)
        if task is None or task.status is not TaskStatus.RUNNING:
            self._write_status(
                snapshot,
                decision="recover_clear",
                reason="task_missing_or_not_running",
            )
            logger.info(
                "watch.recover_clear task_id=%s reason=task_missing_or_not_running",
                state.task_id,
            )
            self._state_store.clear()
            return False

        if (
            snapshot.latest_message_id != state.baseline_message_id
            and (
                snapshot.ready_for_continuation(self._config.idle_threshold)
                or snapshot.soft_stalled(
                    self._config.idle_threshold,
                    self._config.soft_stalled_threshold,
                )
                or snapshot.stalled(self._config.stalled_threshold)
            )
            and snapshot.latest_activity_ms > state.launched_at_ms
        ):
            self._write_status(
                snapshot, decision="recover_done", reason="message_advanced"
            )
            logger.info(
                "watch.recover_done task_id=%s session=%s", task.id, state.session_id
            )
            self._mark_task_done(task.id)
            self._state_store.clear()
            return True

        if (
            snapshot.latest_message_id == state.baseline_message_id
            and snapshot.latest_message_completed_ms is not None
            and snapshot.latest_message_completed_ms > state.launched_at_ms
            and snapshot.ready_for_continuation(self._config.idle_threshold)
        ):
            logger.warning(
                "watch.recover_retry task_id=%s session=%s reason=no_message_advance",
                task.id,
                state.session_id,
            )
            StateMachine.transition(task, TaskStatus.RETRY_WAIT)
            task.error_message = "Continuation did not advance session"
            task.updated_at = datetime.utcnow()
            self._store.update_task(task)
            self._write_status(
                snapshot,
                decision="recover_retry",
                reason="no_message_advance",
                last_error=task.error_message,
            )
            self._state_store.clear()
            return False

        self._write_status(
            snapshot,
            decision="recover_wait",
            reason="awaiting_session_completion",
            running_task_id=task.id,
            active_continuation_task_id=task.id,
        )
        logger.info(
            "watch.recover_wait task_id=%s session=%s", task.id, state.session_id
        )
        return False

    def _mark_task_done(self, task_id: str) -> None:
        task = self._store.get_task(task_id, project_path=self._project_path)
        if task is None:
            return
        if task.status is TaskStatus.RUNNING:
            StateMachine.transition(task, TaskStatus.DONE)
            task.updated_at = datetime.utcnow()
            self._store.update_task(task)
            next_task = self._store.get_next_pending(project_path=self._project_path)
            if self._notifier is not None and hasattr(
                self._notifier, "send_queue_completion_notification"
            ):
                self._notifier.send_queue_completion_notification(task, next_task)
            logger.info("watch.mark_done task_id=%s", task.id)

    def _write_status(
        self,
        snapshot,
        *,
        decision: str,
        reason: str,
        running_task_id: str | None = None,
        active_continuation_task_id: str | None = None,
        last_launch_task_id: str | None = None,
        last_error: str | None = None,
    ) -> None:
        running_task = self._store.get_running_task(project_path=self._project_path)
        pending_task = self._store.get_next_pending(project_path=self._project_path)
        tmux_target = self._tmux_target_store.load()
        self._watcher_status_store.save(
            WatcherStatusSnapshot(
                heartbeat_ms=int(time.time() * 1000),
                session_id=snapshot.root_session_id,
                tmux_session_name=tmux_target.session_name if tmux_target else None,
                tmux_pane_id=tmux_target.pane_id if tmux_target else None,
                tmux_attach_command=tmux_target.attach_command if tmux_target else None,
                latest_message_id=snapshot.latest_message_id,
                latest_message_role=snapshot.latest_message_role,
                latest_message_completed_ms=snapshot.latest_message_completed_ms,
                latest_activity_ms=snapshot.latest_activity_ms,
                is_quiet=snapshot.is_quiet(self._config.idle_threshold),
                ready_for_continuation=snapshot.ready_for_continuation(
                    self._config.idle_threshold
                ),
                soft_stalled=snapshot.soft_stalled(
                    self._config.idle_threshold,
                    self._config.soft_stalled_threshold,
                ),
                stalled=snapshot.stalled(self._config.stalled_threshold),
                decision=decision,
                reason=reason,
                idle_threshold=self._config.idle_threshold,
                soft_stalled_threshold=self._config.soft_stalled_threshold,
                stalled_threshold=self._config.stalled_threshold,
                running_task_id=running_task_id
                or (running_task.id if running_task else None),
                pending_task_id=pending_task.id if pending_task else None,
                active_continuation_task_id=active_continuation_task_id,
                last_launch_task_id=last_launch_task_id,
                last_error=last_error,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watch the current OMO session and continue queued tasks in the same session"
    )
    parser.add_argument("--directory", default=".")
    parser.add_argument("--opencode-db", default=None)
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    log_level = getattr(logging, str(args.log_level).upper(), logging.INFO)
    setup_logging(level=log_level)

    base_dir = Path(args.directory).resolve()
    config = Config.load(base_dir / "omo_task_queue.json")
    store = SQLiteStore(base_dir / "omo_task_queue.db")
    db_path = (
        Path(args.opencode_db).resolve()
        if args.opencode_db
        else Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    )

    observer = OpenCodeObserver(db_path, base_dir)
    notification_settings = config.notification_settings
    notifier = (
        EmailNotifier(
            NotificationConfig(
                enabled=notification_settings.get("enabled", False),
                smtp_host=notification_settings.get("smtp_host", "localhost"),
                smtp_port=notification_settings.get("smtp_port", 587),
                smtp_user=notification_settings.get("smtp_user", ""),
                smtp_password=notification_settings.get("smtp_password", ""),
                smtp_use_tls=notification_settings.get("smtp_use_tls", True),
                smtp_use_ssl=notification_settings.get("smtp_use_ssl", False),
                recipient=notification_settings.get("recipient", ""),
                sender=notification_settings.get("sender", ""),
            )
        )
        if notification_settings.get("enabled")
        else MockNotifier(NotificationConfig(enabled=False))
    )
    continuer = OpencodeSessionContinuer(
        project_dir=base_dir,
        tmux_target_path=base_dir / ".omo_tmux_target.json",
    )
    state_store = ContinuationStateStore(base_dir / ".omo_session_watch_state.json")
    watcher_status_store = WatcherStatusStore(base_dir / ".omo_watcher_status.json")
    tmux_target_store = TmuxTargetStore(base_dir / ".omo_tmux_target.json")

    loop = WatchLoop(
        store=store,
        config=config,
        observer=observer,
        continuer=continuer,
        state_store=state_store,
        watcher_status_store=watcher_status_store,
        tmux_target_store=tmux_target_store,
        project_path=str(base_dir),
        notifier=notifier,
        poll_interval_seconds=args.poll_interval,
    )
    try:
        loop.run_forever()
    finally:
        store.close()


if __name__ == "__main__":
    main()
