from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from omo_task_queue.confirmed_session import ConfirmedSession, ConfirmedSessionStore
from omo_task_queue.state import ExecutionMode, Task, TaskStatus
from omo_task_queue.opencode_observer import OpenCodeObserver
from omo_task_queue.status_provider import QueueStatusProvider
from omo_task_queue.session_selection import (
    ProjectSessionService,
    SessionSelection,
    SessionSelectionStore,
)
from omo_task_queue.store import Config, SQLiteStore
from omo_task_queue.ui.panel import PanelHandler, QueueItem, TestNotificationRequest
from omo_task_queue.ui.server import create_server


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "server_test.db"


@pytest.fixture
def store(tmp_db: Path) -> SQLiteStore:
    return SQLiteStore(tmp_db)


@pytest.fixture
def server(store: SQLiteStore):
    srv = create_server(store, host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _, port = srv.server_address
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def _get(base: str, path: str) -> dict[str, Any]:
    with urlopen(f"{base}{path}", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(base: str, path: str, data: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(data).encode("utf-8")
    req = Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _delete(base: str, path: str) -> dict[str, Any]:
    req = Request(f"{base}{path}", method="DELETE")
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_get_queue_empty(server: str, store: SQLiteStore) -> None:
    data = _get(server, "/api/queue")
    assert data["success"] is True
    assert data["data"] == {"active": [], "completed": []}


def test_post_task_and_list(server: str, store: SQLiteStore) -> None:
    created = _post(
        server,
        "/api/queue",
        {"title": "Test", "prompt": "Do it", "mode": "one_shot", "max_retries": 2},
    )
    assert created["success"] is True
    item = created["data"]
    assert item["title"] == "Test"
    assert item["prompt"] == "Do it"
    assert item["status"] == "pending"
    assert item["max_retries"] == 2

    listed = _get(server, "/api/queue")
    assert len(listed["data"]["active"]) == 1
    assert listed["data"]["active"][0]["title"] == "Test"
    assert listed["data"]["active"][0]["prompt"] == "Do it"


def test_post_task_without_title_derives_prompt_summary(
    server: str, store: SQLiteStore
) -> None:
    created = _post(
        server,
        "/api/queue",
        {"prompt": "Do this very important multi step task now", "mode": "one_shot"},
    )

    assert created["success"] is True
    assert created["data"]["title"] == "Do this very important m…"


def test_retry_task(server: str, store: SQLiteStore) -> None:
    created = _post(server, "/api/queue", {"title": "R", "prompt": "p"})
    task_id = created["data"]["id"]

    resp = _post(server, f"/api/queue/{task_id}/done", {})
    assert resp["success"] is True

    retry_resp = _post(server, f"/api/queue/{task_id}/retry", {})
    assert retry_resp["success"] is True

    listed = _get(server, "/api/queue")
    assert listed["data"]["active"][0]["id"] == task_id
    assert listed["data"]["active"][0]["status"] == "pending"


def test_retry_running_task_rejected(server: str, store: SQLiteStore) -> None:
    created = _post(server, "/api/queue", {"title": "R", "prompt": "p"})
    task_id = created["data"]["id"]
    store.update_status(task_id, TaskStatus.RUNNING)

    retry_resp = _post(server, f"/api/queue/{task_id}/retry", {})
    assert retry_resp["success"] is False
    assert "cannot be retried" in retry_resp["error"].lower()


def test_get_running_none(server: str) -> None:
    data = _get(server, "/api/queue/running")
    assert data["success"] is True
    assert data["data"] is None


def test_reorder_task(server: str, store: SQLiteStore) -> None:
    created = _post(server, "/api/queue", {"title": "R", "prompt": "p"})
    task_id = created["data"]["id"]

    resp = _post(server, "/api/queue/reorder", {"task_id": task_id, "new_order": 99})
    assert resp["success"] is True

    listed = _get(server, "/api/queue")
    assert listed["data"]["active"][0]["order"] == 99


def test_delete_task(server: str, store: SQLiteStore) -> None:
    created = _post(server, "/api/queue", {"title": "D", "prompt": "p"})
    task_id = created["data"]["id"]

    resp = _delete(server, f"/api/queue/{task_id}")
    assert resp["success"] is True

    listed = _get(server, "/api/queue")
    assert listed["data"] == {"active": [], "completed": []}


def test_skip_task(server: str, store: SQLiteStore) -> None:
    created = _post(server, "/api/queue", {"title": "S", "prompt": "p"})
    task_id = created["data"]["id"]

    resp = _post(server, f"/api/queue/{task_id}/skip", {})
    assert resp["success"] is True

    listed = _get(server, "/api/queue")
    assert listed["data"]["completed"][0]["status"] == "skipped"


def test_done_task(server: str, store: SQLiteStore) -> None:
    created = _post(server, "/api/queue", {"title": "D", "prompt": "p"})
    task_id = created["data"]["id"]

    resp = _post(server, f"/api/queue/{task_id}/done", {})
    assert resp["success"] is True

    listed = _get(server, "/api/queue")
    assert listed["data"]["completed"][0]["status"] == "done"


def test_notification_test_no_notifier(server: str) -> None:
    resp = _post(server, "/api/notify/test", {"recipient": "a@b.com"})
    assert resp["success"] is False
    assert "not configured" in resp["error"].lower()


def test_notification_config_get_defaults(server: str) -> None:
    resp = _get(server, "/api/notify/config")
    assert resp["success"] is True
    assert resp["data"]["smtp_host"] == "localhost"
    assert resp["data"]["smtp_port"] == 587


def test_notification_config_save_and_get(server: str) -> None:
    saved = _post(
        server,
        "/api/notify/config",
        {
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 2525,
            "smtp_user": "user",
            "smtp_password": "secret",
            "smtp_use_tls": False,
            "sender": "bot@example.com",
            "recipient": "me@example.com",
        },
    )
    assert saved["success"] is True
    current = _get(server, "/api/notify/config")
    assert current["data"]["enabled"] is True
    assert current["data"]["smtp_host"] == "smtp.example.com"
    assert current["data"]["recipient"] == "me@example.com"


def test_status_endpoint(server: str) -> None:
    data = _get(server, "/api/status")
    assert data["success"] is True
    assert data["data"]["watcher_running"] is False
    assert data["data"]["primary_session_id"] is None
    assert data["data"]["counts"]["pending"] == 0


def test_status_endpoint_with_provider(store: SQLiteStore) -> None:
    def provider() -> dict[str, Any]:
        return {
            "watcher_running": True,
            "primary_session_id": "sess-1",
            "confirmed_session_id": "sess-1",
            "watcher_decision": "waiting",
            "watcher_reason": "not_ready",
            "counts": {"pending": 1},
            "latest_message_role": "assistant",
        }

    srv = create_server(store, host="127.0.0.1", port=0, status_provider=provider)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _, port = srv.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        data = _get(base, "/api/status")
        assert data["success"] is True
        assert data["data"]["watcher_running"] is True
        assert data["data"]["primary_session_id"] == "sess-1"
        assert data["data"]["confirmed_session_id"] == "sess-1"
        assert data["data"]["watcher_reason"] == "not_ready"
        assert data["data"]["latest_message_role"] == "assistant"
    finally:
        srv.shutdown()
        srv.server_close()


def test_queue_status_provider_reads_confirmed_tmux_target(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "queue.db")
    try:
        ConfirmedSessionStore(tmp_path).save(
            ConfirmedSession(
                session_id="sess-7",
                session_short_id=ConfirmedSessionStore.session_short_id("sess-7"),
                project_dir=str(tmp_path.resolve()),
            )
        )
        (tmp_path / ".omo_tmux_target.sess-7.json").write_text(
            json.dumps(
                {
                    "session_name": "omo-confirmed-7",
                    "pane_id": "%7",
                    "attach_command": "tmux attach -t omo-confirmed-7",
                    "project_dir": str(tmp_path.resolve()),
                    "opencode_session_id": "sess-7",
                }
            ),
            encoding="utf-8",
        )
        provider = QueueStatusProvider(
            store=store,
            config=type(
                "Cfg",
                (),
                {
                    "idle_threshold": 60,
                    "soft_stalled_threshold": 300,
                    "stalled_threshold": 600,
                },
            )(),
            project_path=tmp_path,
        )

        data = provider.status()
        assert data["confirmed_session_id"] == "sess-7"
        assert data["tmux_session_name"] == "omo-confirmed-7"
        assert data["tmux_pane_id"] == "%7"
    finally:
        store.close()


def test_sessions_endpoint_returns_selected_and_list(
    tmp_path: Path, store: SQLiteStore
) -> None:
    db_path = tmp_path / "opencode.db"
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
        conn.executemany(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "sess-1",
                    "global",
                    None,
                    "a",
                    str(tmp_path.resolve()),
                    "first",
                    "1",
                    1,
                    1000,
                ),
                (
                    "sess-2",
                    "global",
                    None,
                    "b",
                    str(tmp_path.resolve()),
                    "second",
                    "1",
                    2,
                    3000,
                ),
            ],
        )
    observer = OpenCodeObserver(db_path, tmp_path)
    session_service = ProjectSessionService(
        observer,
        SessionSelectionStore(tmp_path / ".omo_selected_session.json"),
    )
    srv = create_server(
        store, host="127.0.0.1", port=0, session_service=session_service
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _, port = srv.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        data = _get(base, "/api/sessions")
        assert data["success"] is True
        assert data["data"]["selected_session_id"] == "sess-2"
        assert [item["id"] for item in data["data"]["sessions"]] == ["sess-2", "sess-1"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_sessions_select_endpoint_persists_choice(
    tmp_path: Path, store: SQLiteStore
) -> None:
    db_path = tmp_path / "opencode.db"
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
        conn.executemany(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "sess-1",
                    "global",
                    None,
                    "a",
                    str(tmp_path.resolve()),
                    "first",
                    "1",
                    1,
                    1000,
                ),
                (
                    "sess-2",
                    "global",
                    None,
                    "b",
                    str(tmp_path.resolve()),
                    "second",
                    "1",
                    2,
                    3000,
                ),
            ],
        )
    observer = OpenCodeObserver(db_path, tmp_path)
    session_service = ProjectSessionService(
        observer,
        SessionSelectionStore(tmp_path / ".omo_selected_session.json"),
    )
    srv = create_server(
        store, host="127.0.0.1", port=0, session_service=session_service
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _, port = srv.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        selected = _post(base, "/api/sessions/select", {"session_id": "sess-1"})
        assert selected["success"] is True
        assert selected["data"]["selected_session_id"] == "sess-1"
        data = _get(base, "/api/sessions")
        assert data["data"]["selected_session_id"] == "sess-1"
    finally:
        srv.shutdown()
        srv.server_close()


def test_add_task_binds_selected_session(tmp_path: Path, store: SQLiteStore) -> None:
    db_path = tmp_path / "opencode.db"
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
        conn.executemany(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "sess-1",
                    "global",
                    None,
                    "a",
                    str(tmp_path.resolve()),
                    "first",
                    "1",
                    1,
                    1000,
                ),
                (
                    "sess-2",
                    "global",
                    None,
                    "b",
                    str(tmp_path.resolve()),
                    "second",
                    "1",
                    2,
                    3000,
                ),
            ],
        )
    observer = OpenCodeObserver(db_path, tmp_path)
    session_service = ProjectSessionService(
        observer,
        SessionSelectionStore(tmp_path / ".omo_selected_session.json"),
    )
    confirmed_store = ConfirmedSessionStore(tmp_path)
    srv = create_server(
        store,
        host="127.0.0.1",
        port=0,
        project_path=str(tmp_path.resolve()),
        session_resolver=lambda: (
            (confirmed_store.load().session_id if confirmed_store.load() else None)
            or session_service.get_selected_session_id()
        ),
        session_service=session_service,
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _, port = srv.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        _post(base, "/api/sessions/select", {"session_id": "sess-1"})
        confirmed_store.save(
            ConfirmedSession(
                session_id="sess-2",
                session_short_id=ConfirmedSessionStore.session_short_id("sess-2"),
                project_dir=str(tmp_path.resolve()),
            )
        )
        created = _post(base, "/api/queue", {"prompt": "Do it", "mode": "one_shot"})
        assert created["success"] is True
        task = store.get_task(
            created["data"]["id"], project_path=str(tmp_path.resolve())
        )
        assert task is not None
        assert task.target_session_id == "sess-2"
        assert task.project_path == str(tmp_path.resolve())
    finally:
        srv.shutdown()
        srv.server_close()


def test_projects_start_persists_confirmed_session_for_target_project(
    tmp_path: Path, store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    class FakeRegistry:
        def list_projects(self):
            return []

        def upsert(self, **kwargs):
            return None

    popen_calls = []

    class DummyProcess:
        def poll(self):
            return None

        def communicate(self, *args, **kwargs):
            return (b"", b"")

        def kill(self):
            pass

        def wait(self, *args, **kwargs):
            return 0

        def terminate(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_popen(command, stdout=None, stderr=None, cwd=None, env=None, **kwargs):
        popen_calls.append((command, cwd))
        return DummyProcess()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **kw: subprocess.CompletedProcess([], 0, "", "")
    )
    monkeypatch.setattr(
        "omo_task_queue.ui.server.QueueAPIHandler._ensure_project_tmux",
        lambda self, project_path, session_id: {
            "success": True,
            "tmux_session_name": f"omo-test-{session_id}",
            "tmux_pane_id": "%1",
            "attach_command": "tmux attach -t omo-test",
        },
    )

    srv = create_server(
        store,
        host="127.0.0.1",
        port=0,
        project_registry=FakeRegistry(),
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _, port = srv.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        result = _post(
            base,
            "/api/projects/start",
            {"project_path": str(project_dir), "session_id": "sess-9"},
        )
        assert result["success"] is True
        confirmed = ConfirmedSessionStore(project_dir).load()
        assert confirmed is not None
        assert confirmed.session_id == "sess-9"
        selected = SessionSelectionStore(
            project_dir / ".omo_selected_session.json"
        ).load()
        assert selected is not None
        assert selected.session_id == "sess-9"
        assert popen_calls
    finally:
        srv.shutdown()
        srv.server_close()


def test_status_provider_bootstraps_confirmed_from_selected_when_missing(
    tmp_path: Path, store: SQLiteStore
) -> None:
    db_path = tmp_path / "opencode.db"
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
        conn.execute(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sess-boot",
                "global",
                None,
                "boot",
                str(tmp_path.resolve()),
                "boot",
                "1",
                1,
                1000,
            ),
        )
    SessionSelectionStore(tmp_path / ".omo_selected_session.json").save(
        SessionSelection(session_id="sess-boot")
    )
    observer = OpenCodeObserver(db_path, tmp_path)
    session_service = ProjectSessionService(
        observer,
        SessionSelectionStore(tmp_path / ".omo_selected_session.json"),
    )
    provider = QueueStatusProvider(
        store=store,
        config=Config(),
        project_path=tmp_path,
        opencode_db_path=db_path,
        session_service=session_service,
    )

    data = provider.status()

    assert data["confirmed_session_id"] == "sess-boot"
    confirmed = ConfirmedSessionStore(tmp_path).load()
    assert confirmed is not None
    assert confirmed.session_id == "sess-boot"


def test_confirm_switch_retires_old_same_project_state(
    tmp_path: Path, store: SQLiteStore
) -> None:
    db_path = tmp_path / "opencode.db"
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
        for session_id in ("sess-old", "sess-new"):
            conn.execute(
                "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    "global",
                    None,
                    session_id,
                    str(tmp_path.resolve()),
                    session_id,
                    "1",
                    1,
                    1000,
                ),
            )
    ConfirmedSessionStore(tmp_path).save(
        ConfirmedSession(
            session_id="sess-old",
            session_short_id=ConfirmedSessionStore.session_short_id("sess-old"),
            project_dir=str(tmp_path.resolve()),
        )
    )
    running = Task(
        id="run-1",
        title="Run",
        prompt="p",
        mode=ExecutionMode.ONE_SHOT,
        project_path=str(tmp_path.resolve()),
        target_session_id="sess-old",
        status=TaskStatus.RUNNING,
    )
    pending = Task(
        id="pending-1",
        title="Pending",
        prompt="p",
        mode=ExecutionMode.ONE_SHOT,
        project_path=str(tmp_path.resolve()),
        target_session_id="sess-old",
        status=TaskStatus.PENDING,
    )
    store.add_task(running)
    store.add_task(pending)
    (tmp_path / ".omo_session_watch_state.json").write_text(
        json.dumps(
            {
                "task_id": "run-1",
                "session_id": "sess-old",
                "baseline_message_id": "m1",
                "launched_at_ms": 1,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".omo_watcher_status.json").write_text("{}", encoding="utf-8")
    observer = OpenCodeObserver(db_path, tmp_path)
    session_service = ProjectSessionService(
        observer,
        SessionSelectionStore(tmp_path / ".omo_selected_session.json"),
    )
    srv = create_server(
        store,
        host="127.0.0.1",
        port=0,
        project_path=str(tmp_path.resolve()),
        session_service=session_service,
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _, port = srv.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        result = _post(base, "/api/sessions/confirm", {"session_id": "sess-new"})
        assert result["success"] is True
        updated_running = store.get_task("run-1", project_path=str(tmp_path.resolve()))
        updated_pending = store.get_task(
            "pending-1", project_path=str(tmp_path.resolve())
        )
        assert updated_running is not None
        assert updated_pending is not None
        assert updated_running.status == TaskStatus.RETRY_WAIT
        assert updated_running.target_session_id == "sess-new"
        assert updated_pending.target_session_id == "sess-new"
        assert not (tmp_path / ".omo_session_watch_state.json").exists()
        assert not (tmp_path / ".omo_watcher_status.json").exists()
    finally:
        srv.shutdown()
        srv.server_close()


def test_sessions_select_rejects_wrong_project(
    tmp_path: Path, store: SQLiteStore
) -> None:
    db_path = tmp_path / "opencode.db"
    other_dir = tmp_path / "other"
    other_dir.mkdir()
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
        conn.execute(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sess-x",
                "global",
                None,
                "x",
                str(other_dir.resolve()),
                "other",
                "1",
                1,
                1000,
            ),
        )
    observer = OpenCodeObserver(db_path, tmp_path)
    session_service = ProjectSessionService(
        observer,
        SessionSelectionStore(tmp_path / ".omo_selected_session.json"),
    )
    srv = create_server(
        store, host="127.0.0.1", port=0, session_service=session_service
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _, port = srv.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        with pytest.raises(HTTPError) as exc:
            _post(base, "/api/sessions/select", {"session_id": "sess-x"})
        assert exc.value.code == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_static_file_served(tmp_path: Path, store: SQLiteStore) -> None:
    index = tmp_path / "index.html"
    index.write_text("<html>Hello</html>", encoding="utf-8")
    srv = create_server(store, host="127.0.0.1", port=0, static_dir=tmp_path)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _, port = srv.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        with urlopen(base, timeout=5) as resp:
            body = resp.read().decode("utf-8")
        assert body == "<html>Hello</html>"
    finally:
        srv.shutdown()
        srv.server_close()


def test_404_unknown_endpoint(server: str) -> None:
    with pytest.raises(Exception):
        _get(server, "/api/unknown")
