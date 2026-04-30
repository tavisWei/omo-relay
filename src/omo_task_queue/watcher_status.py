from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from omo_task_queue.tmux_target import normalize_attach_command


@dataclass
class WatcherStatusSnapshot:
    heartbeat_ms: int
    session_id: Optional[str]
    latest_message_id: Optional[str]
    latest_message_role: Optional[str]
    latest_message_completed_ms: Optional[int]
    latest_activity_ms: int
    is_quiet: bool
    ready_for_continuation: bool
    soft_stalled: bool
    stalled: bool
    decision: str
    reason: str
    idle_threshold: int
    soft_stalled_threshold: int
    stalled_threshold: int
    running_task_id: Optional[str] = None
    pending_task_id: Optional[str] = None
    active_continuation_task_id: Optional[str] = None
    last_launch_task_id: Optional[str] = None
    last_error: Optional[str] = None
    tmux_session_name: Optional[str] = None
    tmux_pane_id: Optional[str] = None
    tmux_attach_command: Optional[str] = None


class WatcherStatusStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> Optional[WatcherStatusSnapshot]:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        if data.get("tmux_session_name"):
            data["tmux_attach_command"] = normalize_attach_command(
                data.get("tmux_attach_command"),
                tmux_executable=Path.home() / ".local" / "bin" / "tmux",
                session_name=data["tmux_session_name"],
            )
        return WatcherStatusSnapshot(**data)

    def save(self, snapshot: WatcherStatusSnapshot) -> None:
        self._path.write_text(
            json.dumps(asdict(snapshot), indent=2, sort_keys=True),
            encoding="utf-8",
        )
