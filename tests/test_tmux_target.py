from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from omo_task_queue.tmux_target import (
    TmuxTarget,
    TmuxTargetStore,
    build_opencode_launch_command,
    resolve_opencode_executable,
)
from omo_task_queue.watcher_status import WatcherStatusStore


def test_tmux_target_store_normalizes_legacy_attach_command(tmp_path: Path) -> None:
    target_path = tmp_path / ".omo_tmux_target.json"
    target_path.write_text(
        json.dumps(
            {
                "session_name": "omo-test",
                "pane_id": "%1",
                "attach_command": "tmux attach -t omo-test",
                "project_dir": str(tmp_path),
                "opencode_session_id": "ses-1",
            }
        ),
        encoding="utf-8",
    )

    store = TmuxTargetStore(target_path, tmux_executable="/custom/tmux")

    target = store.load()

    assert target is not None
    assert target.attach_command == "/custom/tmux attach -t omo-test"


def test_tmux_target_store_save_persists_normalized_attach_command(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / ".omo_tmux_target.json"
    store = TmuxTargetStore(target_path, tmux_executable="/custom/tmux")

    store.save(
        TmuxTarget(
            session_name="omo-test",
            pane_id="%1",
            attach_command="tmux attach -t omo-test",
            project_dir=str(tmp_path),
            opencode_session_id="ses-1",
        )
    )

    payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert payload["attach_command"] == "/custom/tmux attach -t omo-test"


def test_watcher_status_store_normalizes_legacy_attach_command(tmp_path: Path) -> None:
    status_path = tmp_path / ".omo_watcher_status.json"
    status_path.write_text(
        json.dumps(
            {
                "heartbeat_ms": 1,
                "session_id": "ses-1",
                "latest_message_id": None,
                "latest_message_role": None,
                "latest_message_completed_ms": None,
                "latest_activity_ms": 1,
                "is_quiet": False,
                "ready_for_continuation": False,
                "soft_stalled": False,
                "stalled": False,
                "decision": "waiting",
                "reason": "not_ready",
                "idle_threshold": 3,
                "soft_stalled_threshold": 3,
                "stalled_threshold": 3,
                "tmux_session_name": "omo-test",
                "tmux_pane_id": "%1",
                "tmux_attach_command": "tmux attach -t omo-test",
            }
        ),
        encoding="utf-8",
    )

    snapshot = WatcherStatusStore(status_path).load()

    assert snapshot is not None
    assert (
        snapshot.tmux_attach_command
        == str(Path.home() / ".local" / "bin" / "tmux") + " attach -t omo-test"
    )


def test_validate_target_requires_pane_id_to_exist(monkeypatch, tmp_path: Path) -> None:
    target_path = tmp_path / ".omo_tmux_target.json"
    target_path.write_text(
        json.dumps(
            {
                "session_name": "omo-test",
                "pane_id": "%1",
                "attach_command": "tmux attach -t omo-test",
                "project_dir": str(tmp_path.resolve()),
                "opencode_session_id": "ses-1",
            }
        ),
        encoding="utf-8",
    )
    store = TmuxTargetStore(target_path)

    def fake_run(command, capture_output, text, check, env=None):
        if command[1:3] == ["has-session", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%2\n", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    valid, reason = store.validate_target(
        expected_project_dir=tmp_path.resolve(),
        expected_session_id="ses-1",
    )

    assert valid is False
    assert reason == "tmux pane missing"


def test_build_opencode_launch_command_uses_exec() -> None:
    command = build_opencode_launch_command("ses-1")
    opencode_executable = resolve_opencode_executable()

    assert f"exec {opencode_executable} -s ses-1 ." in command


def test_ensure_target_rejects_unstable_session(monkeypatch, tmp_path: Path) -> None:
    target_path = tmp_path / ".omo_tmux_target.json"
    store = TmuxTargetStore(target_path)
    calls = {"has_session": 0}

    def fake_run(command, capture_output, text, check, env=None):
        if command[1:2] == ["new-session"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%9\n", stderr="")
        if command[1:3] == ["has-session", "-t"]:
            calls["has_session"] += 1
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="gone")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="tmux pane not ready"):
        store.ensure_target(project_dir=tmp_path, opencode_session_id="ses-1")


def test_ensure_target_uses_unique_session_name_when_base_exists(
    monkeypatch, tmp_path: Path
) -> None:
    target_path = tmp_path / ".omo_tmux_target.json"
    store = TmuxTargetStore(target_path)
    commands: list[list[str]] = []

    def fake_run(command, capture_output, text, check, env=None):
        commands.append(command)
        if command[1:3] == ["kill-session", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:2] == ["new-session"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%9\n", stderr="")
        if command[1:3] == ["has-session", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.tmux_target.time.sleep", lambda _: None)

    target = store.ensure_target(project_dir=tmp_path, opencode_session_id="ses-1")

    assert any(command[1:3] == ["kill-session", "-t"] for command in commands)
    assert target.session_name.startswith("omo-")
    assert target.session_name.count("-") == 1


def test_ensure_target_clears_stale_metadata_when_existing_target_invalid(
    monkeypatch, tmp_path: Path
) -> None:
    target_path = tmp_path / ".omo_tmux_target.json"
    target_path.write_text(
        json.dumps(
            {
                "session_name": "omo-stale",
                "pane_id": "%1",
                "attach_command": "tmux attach -t omo-stale",
                "project_dir": str(tmp_path.resolve()),
                "opencode_session_id": "ses-1",
            }
        ),
        encoding="utf-8",
    )
    store = TmuxTargetStore(target_path)
    created_sessions: list[str] = []

    def fake_run(command, capture_output, text, check, env=None):
        if command[1:3] == ["kill-session", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:2] == ["new-session"]:
            session_name = command[command.index("-s") + 1]
            created_sessions.append(session_name)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["has-session", "-t"]:
            session_name = command[-1]
            if created_sessions and session_name == created_sessions[-1]:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="gone")
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%9\n", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.tmux_target.time.sleep", lambda _: None)

    target = store.ensure_target(project_dir=tmp_path, opencode_session_id="ses-1")

    assert target.session_name != "omo-stale"
    assert target.session_name.startswith("omo-")
    assert (
        json.loads(target_path.read_text(encoding="utf-8"))["session_name"]
        == target.session_name
    )


def test_ensure_target_reuses_fixed_project_session_name(
    monkeypatch, tmp_path: Path
) -> None:
    target_path = tmp_path / ".omo_tmux_target.json"
    target_path.write_text(
        json.dumps(
            {
                "session_name": "omo-old-fixed",
                "pane_id": "%1",
                "attach_command": "tmux attach -t omo-old-fixed",
                "project_dir": str(tmp_path.resolve()),
                "opencode_session_id": "ses-1",
            }
        ),
        encoding="utf-8",
    )
    store = TmuxTargetStore(target_path)
    created_sessions: list[str] = []

    def fake_run(command, capture_output, text, check, env=None):
        if command[1:3] == ["kill-session", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["has-session", "-t"]:
            session_name = command[-1]
            if created_sessions and session_name == created_sessions[-1]:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="gone")
        if command[1:2] == ["new-session"]:
            session_name = command[command.index("-s") + 1]
            created_sessions.append(session_name)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%9\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.tmux_target.time.sleep", lambda _: None)

    target = store.ensure_target(project_dir=tmp_path, opencode_session_id="ses-1")

    assert created_sessions
    assert created_sessions[0] != "omo-old-fixed"
    assert created_sessions[0].startswith("omo-")
    assert created_sessions[0].count("-") == 1
    assert target.session_name == created_sessions[0]


def test_ensure_target_clears_metadata_after_pane_not_ready_failure(
    monkeypatch, tmp_path: Path
) -> None:
    target_path = tmp_path / ".omo_tmux_target.json"
    store = TmuxTargetStore(target_path)

    def fake_run(command, capture_output, text, check, env=None):
        if command[1:3] == ["kill-session", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:2] == ["new-session"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%9\n", stderr="")
        if command[1:3] == ["has-session", "-t"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="gone")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.tmux_target.time.sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="tmux pane not ready"):
        store.ensure_target(project_dir=tmp_path, opencode_session_id="ses-1")

    assert not target_path.exists()


def test_ensure_target_prefers_restart_script_and_reloads_saved_target(
    monkeypatch, tmp_path: Path
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script_path = scripts_dir / "restart-opencode-tmux.sh"
    script_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    target_path = tmp_path / ".omo_tmux_target.json"
    store = TmuxTargetStore(target_path)
    calls: list[list[str]] = []

    def fake_run(command, capture_output, text, check, env=None, cwd=None):
        calls.append(command)
        if command == [str(script_path)]:
            target_path.write_text(
                json.dumps(
                    {
                        "session_name": "omo-scripted",
                        "pane_id": "%7",
                        "attach_command": "/custom/tmux attach -t omo-scripted",
                        "project_dir": str(tmp_path.resolve()),
                        "opencode_session_id": "ses-1",
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
        if command[1:3] == ["has-session", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%7\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    target = store.ensure_target(project_dir=tmp_path, opencode_session_id="ses-1")

    assert calls[0] == [str(script_path)]
    assert target.session_name == "omo-scripted"
    assert target.pane_id == "%7"
