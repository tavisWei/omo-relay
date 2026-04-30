from __future__ import annotations

import subprocess
import json
from pathlib import Path

from omo_task_queue.session_continuer import OpencodeSessionContinuer
from omo_task_queue.state import ExecutionMode, Task


def make_task(
    task_id: str,
    mode: ExecutionMode,
    prompt: str = "Prompt",
    *,
    project_path: str = "",
    target_session_id: str | None = None,
) -> Task:
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        prompt=prompt,
        mode=mode,
        project_path=project_path,
        target_session_id=target_session_id,
    )


def test_continue_task_builds_one_shot_command(monkeypatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(command, capture_output, text, check, env=None):
        captured.append(command)
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%1\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
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
    continuer = OpencodeSessionContinuer(
        project_dir=tmp_path,
        tmux_target_path=target_path,
    )

    continuer.continue_task(
        "ses-1",
        make_task(
            "t1",
            ExecutionMode.ONE_SHOT,
            "hello",
            project_path=str(tmp_path.resolve()),
            target_session_id="ses-1",
        ),
    )

    send_keys_calls = [command for command in captured if "send-keys" in command]

    assert send_keys_calls[0] == [
        str(Path.home() / ".local" / "bin" / "tmux"),
        "send-keys",
        "-t",
        "%1",
        "hello",
    ]
    assert send_keys_calls[1] == [
        str(Path.home() / ".local" / "bin" / "tmux"),
        "send-keys",
        "-t",
        "%1",
        "Enter",
    ]


def test_continue_task_prefixes_loop_commands(monkeypatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(command, capture_output, text, check, env=None):
        captured.append(command)
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%1\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
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
    continuer = OpencodeSessionContinuer(
        project_dir=tmp_path,
        tmux_target_path=target_path,
    )

    continuer.continue_task(
        "ses-1",
        make_task(
            "t1",
            ExecutionMode.ULW_LOOP,
            "alpha",
            project_path=str(tmp_path.resolve()),
            target_session_id="ses-1",
        ),
    )
    continuer.continue_task(
        "ses-1",
        make_task(
            "t2",
            ExecutionMode.RALPH_LOOP,
            "beta",
            project_path=str(tmp_path.resolve()),
            target_session_id="ses-1",
        ),
    )

    send_keys_calls = [command for command in captured if "send-keys" in command]

    assert send_keys_calls[0][-1] == "/ulw-loop alpha"
    assert send_keys_calls[1][-1] == "Enter"
    assert send_keys_calls[2][-1] == "/ralph-loop beta"
    assert send_keys_calls[3][-1] == "Enter"


def test_continue_task_normalizes_literal_newlines(monkeypatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(command, capture_output, text, check, env=None):
        captured.append(command)
        if command[1:3] == ["list-panes", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="%1\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
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
    continuer = OpencodeSessionContinuer(
        project_dir=tmp_path,
        tmux_target_path=target_path,
    )

    continuer.continue_task(
        "ses-1",
        make_task(
            "t1",
            ExecutionMode.ONE_SHOT,
            "line1\\nline2",
            project_path=str(tmp_path.resolve()),
            target_session_id="ses-1",
        ),
    )

    send_keys_calls = [command for command in captured if "send-keys" in command]
    assert send_keys_calls[0][-1] == "line1\nline2"
    assert send_keys_calls[1][-1] == "Enter"


def test_continue_task_preserves_existing_trailing_marker() -> None:
    task = make_task("t1", ExecutionMode.ONE_SHOT, "hello/n")

    assert OpencodeSessionContinuer._build_prompt(task) == "hello/n"


def test_continue_task_strips_space_before_trailing_marker() -> None:
    task = make_task("t1", ExecutionMode.ONE_SHOT, "hello /n")

    assert OpencodeSessionContinuer._build_prompt(task) == "hello/n"


def test_continue_task_auto_creates_target_when_missing(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []

    def fake_run(command, capture_output, text, check, env=None):
        captured.append(command)
        if command[1:2] == ["new-session"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["list-panes", "-t"]:
            if command[-1] == "#{pane_id}":
                return subprocess.CompletedProcess(command, 0, stdout="%9\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="%9\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    target_path = tmp_path / ".omo_tmux_target.json"
    continuer = OpencodeSessionContinuer(
        project_dir=tmp_path,
        tmux_target_path=target_path,
    )

    result = continuer.continue_task(
        "ses-2",
        make_task(
            "t1",
            ExecutionMode.ONE_SHOT,
            "hello",
            project_path=str(tmp_path.resolve()),
            target_session_id="ses-2",
        ),
    )

    assert result.returncode == 0
    assert any(command[1:2] == ["new-session"] for command in captured)
    assert target_path.exists()


def test_continue_task_recreates_target_when_saved_pane_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []

    def fake_run(command, capture_output, text, check, env=None):
        captured.append(command)
        if command[1:3] == ["has-session", "-t"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["list-panes", "-t"]:
            if command[-1] == "#{pane_id}":
                session_name = command[3]
                if session_name == "omo-stale":
                    return subprocess.CompletedProcess(
                        command, 0, stdout="%2\n", stderr=""
                    )
                return subprocess.CompletedProcess(command, 0, stdout="%9\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:2] == ["new-session"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    target_path = tmp_path / ".omo_tmux_target.json"
    target_path.write_text(
        json.dumps(
            {
                "session_name": "omo-stale",
                "pane_id": "%1",
                "attach_command": "tmux attach -t omo-stale",
                "project_dir": str(tmp_path.resolve()),
                "opencode_session_id": "ses-2",
            }
        ),
        encoding="utf-8",
    )
    continuer = OpencodeSessionContinuer(
        project_dir=tmp_path,
        tmux_target_path=target_path,
    )

    result = continuer.continue_task(
        "ses-2",
        make_task(
            "t1",
            ExecutionMode.ONE_SHOT,
            "hello",
            project_path=str(tmp_path.resolve()),
            target_session_id="ses-2",
        ),
    )

    assert result.returncode == 0
    assert any(command[1:2] == ["new-session"] for command in captured)
    send_keys_calls = [command for command in captured if "send-keys" in command]
    assert send_keys_calls[0][3] == "%9"
