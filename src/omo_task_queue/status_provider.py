from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from omo_task_queue.confirmed_session import (
    ConfirmedSessionStore,
    resolve_confirmed_session_id,
)
from omo_task_queue.opencode_observer import OpenCodeObserver, SessionSnapshot
from omo_task_queue.session_selection import ProjectSessionService
from omo_task_queue.session_continuer import ContinuationStateStore
from omo_task_queue.store import Config, SQLiteStore
from omo_task_queue.tmux_target import TmuxTargetStore
from omo_task_queue.watcher_status import WatcherStatusSnapshot, WatcherStatusStore


def _iso_from_ms(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return datetime.utcfromtimestamp(value / 1000).isoformat()


class QueueStatusProvider:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        config: Config,
        project_path: str | Path,
        opencode_db_path: str | Path | None = None,
        watcher_status_path: str | Path | None = None,
        continuation_state_path: str | Path | None = None,
        session_service: ProjectSessionService | None = None,
        watcher_freshness_seconds: int = 15,
    ) -> None:
        self._store = store
        self._config = config
        self._project_path = str(Path(project_path).resolve())
        self._observer = None
        if opencode_db_path is not None and Path(opencode_db_path).exists():
            self._observer = OpenCodeObserver(opencode_db_path, self._project_path)
        self._watcher_status = (
            WatcherStatusStore(watcher_status_path) if watcher_status_path else None
        )
        self._continuation_state = (
            ContinuationStateStore(continuation_state_path)
            if continuation_state_path
            else None
        )
        self._session_service = session_service
        self._watcher_freshness_seconds = watcher_freshness_seconds

    def status(self) -> dict[str, Any]:
        counts = {
            "pending": 0,
            "running": 0,
            "retry_wait": 0,
            "done": 0,
            "skipped": 0,
        }
        tasks = self._store.list_tasks(project_path=self._project_path)
        for task in tasks:
            counts[task.status.value] = counts.get(task.status.value, 0) + 1

        running_task = self._store.get_running_task(project_path=self._project_path)
        pending_task = self._store.get_next_pending(project_path=self._project_path)
        session_id = None
        snapshot = None
        selected_session_id = (
            self._session_service.get_selected_session_id()
            if self._session_service is not None
            else None
        )
        confirmed_session_id = resolve_confirmed_session_id(
            self._project_path, selected_session_id
        )
        if self._observer is not None:
            session_id = (
                confirmed_session_id
                or selected_session_id
                or self._observer.locate_primary_session()
            )
            if session_id is not None:
                snapshot = self._observer.snapshot(session_id)

        watcher_snapshot = self._watcher_status.load() if self._watcher_status else None
        continuation_state = (
            self._continuation_state.load() if self._continuation_state else None
        )
        live_tmux_target = self._load_tmux_target(confirmed_session_id)

        return {
            "project_path": self._project_path,
            "counts": counts,
            "primary_session_id": session_id,
            "selected_session_id": selected_session_id,
            "confirmed_session_id": confirmed_session_id,
            "tmux_session_name": (
                (live_tmux_target.session_name if live_tmux_target else None)
                or (watcher_snapshot.tmux_session_name if watcher_snapshot else None)
            ),
            "tmux_pane_id": (
                (live_tmux_target.pane_id if live_tmux_target else None)
                or (watcher_snapshot.tmux_pane_id if watcher_snapshot else None)
            ),
            "tmux_attach_command": (
                (live_tmux_target.attach_command if live_tmux_target else None)
                or (watcher_snapshot.tmux_attach_command if watcher_snapshot else None)
            ),
            "watcher_running": self._watcher_running(watcher_snapshot),
            "watcher_decision": watcher_snapshot.decision if watcher_snapshot else None,
            "watcher_reason": watcher_snapshot.reason if watcher_snapshot else None,
            "watcher_last_checked_at": _iso_from_ms(
                watcher_snapshot.heartbeat_ms if watcher_snapshot else None
            ),
            "watcher_last_launch_task_id": (
                watcher_snapshot.last_launch_task_id if watcher_snapshot else None
            ),
            "watcher_last_error": watcher_snapshot.last_error
            if watcher_snapshot
            else None,
            "active_continuation_task_id": (
                continuation_state.task_id if continuation_state else None
            ),
            "running_task_id": running_task.id if running_task else None,
            "pending_task_id": pending_task.id if pending_task else None,
            "latest_message_id": self._pick(
                watcher_snapshot, snapshot, "latest_message_id"
            ),
            "latest_message_role": self._pick(
                watcher_snapshot, snapshot, "latest_message_role"
            ),
            "latest_message_completed_ms": self._pick(
                watcher_snapshot, snapshot, "latest_message_completed_ms"
            ),
            "latest_message_completed_at": _iso_from_ms(
                self._pick(watcher_snapshot, snapshot, "latest_message_completed_ms")
            ),
            "latest_activity_ms": self._pick(
                watcher_snapshot, snapshot, "latest_activity_ms"
            ),
            "latest_activity_at": _iso_from_ms(
                self._pick(watcher_snapshot, snapshot, "latest_activity_ms")
            ),
            "is_quiet": self._pick_bool(watcher_snapshot, snapshot, "is_quiet"),
            "ready_for_continuation": self._pick_bool(
                watcher_snapshot, snapshot, "ready_for_continuation"
            ),
            "soft_stalled": self._pick_bool(watcher_snapshot, snapshot, "soft_stalled"),
            "stalled": self._pick_bool(watcher_snapshot, snapshot, "stalled"),
            "idle_threshold": self._config.idle_threshold,
            "soft_stalled_threshold": self._config.soft_stalled_threshold,
            "stalled_threshold": self._config.stalled_threshold,
        }

    def _watcher_running(self, snapshot: Optional[WatcherStatusSnapshot]) -> bool:
        if snapshot is None:
            return False
        return int(time.time() * 1000) - snapshot.heartbeat_ms <= (
            self._watcher_freshness_seconds * 1000
        )

    def _pick(
        self,
        watcher_snapshot: Optional[WatcherStatusSnapshot],
        observer_snapshot: Optional[SessionSnapshot],
        field: str,
    ) -> Any:
        if watcher_snapshot is not None and hasattr(watcher_snapshot, field):
            return getattr(watcher_snapshot, field)
        if observer_snapshot is not None and hasattr(observer_snapshot, field):
            return getattr(observer_snapshot, field)
        return None

    def _pick_bool(
        self,
        watcher_snapshot: Optional[WatcherStatusSnapshot],
        observer_snapshot: Optional[SessionSnapshot],
        field: str,
    ) -> bool:
        if watcher_snapshot is not None and hasattr(watcher_snapshot, field):
            return bool(getattr(watcher_snapshot, field))
        if observer_snapshot is None:
            return False
        if field == "is_quiet":
            return observer_snapshot.is_quiet(self._config.idle_threshold)
        if field == "ready_for_continuation":
            return observer_snapshot.ready_for_continuation(self._config.idle_threshold)
        if field == "soft_stalled":
            return observer_snapshot.soft_stalled(
                self._config.idle_threshold,
                self._config.soft_stalled_threshold,
            )
        if field == "stalled":
            return observer_snapshot.stalled(self._config.stalled_threshold)
        return False

    def _load_tmux_target(self, confirmed_session_id: Optional[str]):
        if not confirmed_session_id:
            return None
        short_id = ConfirmedSessionStore.session_short_id(confirmed_session_id)
        target_path = Path(self._project_path) / f".omo_tmux_target.{short_id}.json"
        return TmuxTargetStore(target_path).load()
