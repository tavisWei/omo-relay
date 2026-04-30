from __future__ import annotations

import sqlite3
from pathlib import Path

from omo_task_queue.opencode_observer import OpenCodeObserver


def _seed_schema(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                parent_id TEXT,
                slug TEXT NOT NULL,
                directory TEXT NOT NULL,
                title TEXT NOT NULL,
                version TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL
            );
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            """
        )


def test_locate_primary_session_prefers_latest_root(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    _seed_schema(db_path)
    directory = str((tmp_path / "project").resolve())

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("root-1", "global", None, "a", directory, "first", "1", 1, 1000),
                ("root-2", "global", None, "b", directory, "second", "1", 2, 3000),
                ("child-1", "global", "root-2", "c", directory, "child", "1", 3, 4000),
            ],
        )

    observer = OpenCodeObserver(db_path, directory)
    assert observer.locate_primary_session() == "root-2"


def test_snapshot_tracks_primary_session_only_and_latest_activity(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "opencode.db"
    _seed_schema(db_path)
    directory = str((tmp_path / "project").resolve())

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("root", "global", None, "root", directory, "root", "1", 1, 1000),
                ("child", "global", "root", "child", directory, "child", "1", 2, 2000),
            ],
        )
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (
                "msg-1",
                "root",
                2500,
                2500,
                '{"role": "assistant", "time": {"created": 2500, "completed": 4500}}',
            ),
        )
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (
                "msg-child",
                "child",
                5000,
                5000,
                '{"role": "assistant", "time": {"created": 5000, "completed": 6000}}',
            ),
        )

    observer = OpenCodeObserver(db_path, directory)
    snapshot = observer.snapshot("root")

    assert snapshot.session_ids == ("root",)
    assert snapshot.latest_message_id == "msg-1"
    assert snapshot.latest_message_role == "assistant"
    assert snapshot.latest_activity_ms == 4500
    assert snapshot.is_quiet(1, now_ms=6000) is True
    assert snapshot.is_quiet(2, now_ms=6000) is False
    assert snapshot.ready_for_continuation(1, now_ms=6000) is True


def test_snapshot_not_ready_when_latest_message_is_user(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    _seed_schema(db_path)
    directory = str((tmp_path / "project").resolve())

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("root", "global", None, "root", directory, "root", "1", 1, 1000),
        )
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (
                "msg-user",
                "root",
                2500,
                2500,
                '{"role": "user", "time": {"created": 2500}}',
            ),
        )

    observer = OpenCodeObserver(db_path, directory)
    snapshot = observer.snapshot("root")

    assert snapshot.latest_message_role == "user"
    assert snapshot.latest_message_completed_ms is None
    assert snapshot.ready_for_continuation(1, now_ms=6000) is False
    assert snapshot.soft_stalled(1, 3, now_ms=6000) is False
    assert snapshot.stalled(3, now_ms=6000) is True


def test_snapshot_soft_stalled_for_quiet_assistant_without_completed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "opencode.db"
    _seed_schema(db_path)
    directory = str((tmp_path / "project").resolve())

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("root", "global", None, "root", directory, "root", "1", 1, 1000),
        )
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (
                "msg-assistant",
                "root",
                2500,
                2500,
                '{"role": "assistant", "time": {"created": 2500}}',
            ),
        )

    observer = OpenCodeObserver(db_path, directory)
    snapshot = observer.snapshot("root")

    assert snapshot.ready_for_continuation(1, now_ms=6000) is False
    assert snapshot.soft_stalled(1, 3, now_ms=6000) is True
