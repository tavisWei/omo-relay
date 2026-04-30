from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectSession:
    id: str
    directory: str
    title: str
    time_updated: int


@dataclass(frozen=True)
class SessionSnapshot:
    root_session_id: str
    session_ids: tuple[str, ...]
    latest_activity_ms: int
    latest_message_id: str | None
    latest_message_completed_ms: int | None
    latest_message_role: str | None

    def is_quiet(self, quiet_seconds: int, now_ms: int | None = None) -> bool:
        effective_now = now_ms if now_ms is not None else int(time.time() * 1000)
        return effective_now - self.latest_activity_ms >= quiet_seconds * 1000

    def ready_for_continuation(
        self, quiet_seconds: int, now_ms: int | None = None
    ) -> bool:
        return (
            self.latest_message_role == "assistant"
            and self.latest_message_completed_ms is not None
            and self.is_quiet(quiet_seconds, now_ms=now_ms)
        )

    def stalled(self, stalled_seconds: int, now_ms: int | None = None) -> bool:
        effective_now = now_ms if now_ms is not None else int(time.time() * 1000)
        return effective_now - self.latest_activity_ms >= stalled_seconds * 1000

    def soft_stalled(
        self, quiet_seconds: int, soft_stalled_seconds: int, now_ms: int | None = None
    ) -> bool:
        effective_now = now_ms if now_ms is not None else int(time.time() * 1000)
        return (
            self.latest_message_role == "assistant"
            and self.latest_message_completed_ms is None
            and self.is_quiet(quiet_seconds, now_ms=effective_now)
            and effective_now - self.latest_activity_ms >= soft_stalled_seconds * 1000
        )


class OpenCodeObserver:
    def __init__(self, db_path: str | Path, project_dir: str | Path) -> None:
        self._db_path = str(db_path)
        self._project_dir = str(Path(project_dir).resolve())

    def locate_primary_session(self) -> str | None:
        sessions = self.list_project_sessions()
        return sessions[0].id if sessions else None

    def list_project_sessions(self) -> list[ProjectSession]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, directory, title, time_updated
                FROM session
                WHERE directory = ? AND parent_id IS NULL
                ORDER BY time_updated DESC
                """,
                (self._project_dir,),
            ).fetchall()
        return [
            ProjectSession(
                id=str(row[0]),
                directory=str(row[1]),
                title=str(row[2]),
                time_updated=int(row[3]),
            )
            for row in rows
        ]

    def session_exists(self, session_id: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM session WHERE id = ? LIMIT 1", (session_id,)
            ).fetchone()
        return row is not None

    def session_belongs_to_project(self, session_id: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT directory FROM session WHERE id = ? LIMIT 1", (session_id,)
            ).fetchone()
        if row is None:
            return False
        return str(Path(row[0]).resolve()) == self._project_dir

    def snapshot(self, root_session_id: str) -> SessionSnapshot:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            session_ids = (root_session_id,)
            placeholders = ",".join("?" for _ in session_ids)

            session_row = conn.execute(
                f"SELECT MAX(time_updated) AS latest_time_updated FROM session WHERE id IN ({placeholders})",
                session_ids,
            ).fetchone()
            latest_time_updated = int(session_row["latest_time_updated"] or 0)

            message_row = conn.execute(
                f"""
                SELECT id, time_created, data
                FROM message
                WHERE session_id IN ({placeholders})
                ORDER BY time_created DESC
                LIMIT 1
                """,
                session_ids,
            ).fetchone()

        latest_message_id: str | None = None
        latest_message_time = 0
        latest_message_completed_ms: int | None = None
        latest_message_role: str | None = None
        if message_row is not None:
            latest_message_id = str(message_row["id"])
            latest_message_time = int(message_row["time_created"] or 0)
            latest_message_role = self._extract_role(str(message_row["data"]))
            latest_message_completed_ms = self._extract_completed_time(
                str(message_row["data"])
            )

        latest_activity_ms = max(
            latest_time_updated,
            latest_message_completed_ms or 0,
            latest_message_time,
        )

        return SessionSnapshot(
            root_session_id=root_session_id,
            session_ids=session_ids,
            latest_activity_ms=latest_activity_ms,
            latest_message_id=latest_message_id,
            latest_message_completed_ms=latest_message_completed_ms,
            latest_message_role=latest_message_role,
        )

    @staticmethod
    def _extract_completed_time(raw_data: str) -> int | None:
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            return None
        time_data = data.get("time")
        if not isinstance(time_data, dict):
            return None
        completed = time_data.get("completed")
        return int(completed) if isinstance(completed, int) else None

    @staticmethod
    def _extract_role(raw_data: str) -> str | None:
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            return None
        role = data.get("role")
        return str(role) if isinstance(role, str) else None
