from __future__ import annotations

import subprocess
import json
from pathlib import Path

from omo_task_queue.session_continuer import OpencodeSessionContinuer
from omo_task_queue.state import ExecutionMode, Task


def make_task(
    task_id, mode, prompt="Prompt", *, project_path="", target_session_id=None
):
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        prompt=prompt,
        mode=mode,
        project_path=project_path,
        target_session_id=target_session_id,
    )


def _completed(cmd, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, 0, stdout, stderr)


def test_continue_task_pastes_prompt(monkeypatch, tmp_path):
    captured = []

    def fake_run(*args, **kwargs):
        cmd = args[0]
        captured.append(cmd)
        if cmd[1:3] == ["capture-pane", "-t"]:
            return _completed(cmd, stdout="OpenCode")
        if cmd[1:3] == ["list-panes", "-t"]:
            if "#{pane_current_command}" in cmd:
                return _completed(cmd, stdout="node\n")
            return _completed(cmd, stdout="%1\n")
        if len(cmd) > 1 and cmd[1].endswith("restart-opencode-tmux-generic.sh"):
            tf = Path(cmd[-1])
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(
                json.dumps(
                    {
                        "session_name": cmd[3],
                        "pane_id": "%1",
                        "attach_command": f"tmux attach -t {cmd[3]}",
                        "project_dir": cmd[4],
                        "opencode_session_id": cmd[5],
                    }
                ),
                encoding="utf-8",
            )
            return _completed(cmd)
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.session_continuer.time.sleep", lambda _: None)
    c = OpencodeSessionContinuer(
        project_dir=tmp_path, tmux_target_path=tmp_path / ".omo_tmux_target.json"
    )
    r = c.continue_task(
        "ses-1",
        make_task(
            "t1", ExecutionMode.ONE_SHOT, "hello", project_path=str(tmp_path.resolve())
        ),
    )
    assert r.returncode == 0


def test_continue_task_warmup_when_pane_not_ready(monkeypatch, tmp_path):
    captured = []
    ready = {"count": 0}

    def fake_run(*args, **kwargs):
        cmd = args[0]
        captured.append(cmd)
        if cmd[1:3] == ["capture-pane", "-t"]:
            ready["count"] += 1
            if ready["count"] <= 3:
                return _completed(cmd, stdout="bash")
            return _completed(cmd, stdout="OpenCode")
        if cmd[1:3] == ["list-panes", "-t"]:
            if "#{pane_current_command}" in cmd:
                return _completed(cmd, stdout="node\n")
            return _completed(cmd, stdout="%9\n")
        if len(cmd) > 1 and cmd[1].endswith("restart-opencode-tmux-generic.sh"):
            tf = Path(cmd[-1])
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(
                json.dumps(
                    {
                        "session_name": cmd[3],
                        "pane_id": "%9",
                        "attach_command": f"tmux attach -t {cmd[3]}",
                        "project_dir": cmd[4],
                        "opencode_session_id": cmd[5],
                    }
                ),
                encoding="utf-8",
            )
            return _completed(cmd)
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.session_continuer.time.sleep", lambda _: None)
    c = OpencodeSessionContinuer(
        project_dir=tmp_path, tmux_target_path=tmp_path / ".omo_tmux_target.ses-2.json"
    )
    r = c.continue_task(
        "ses-2",
        make_task(
            "t1",
            ExecutionMode.ONE_SHOT,
            "hello",
            project_path=str(tmp_path.resolve()),
            target_session_id="ses-2",
        ),
    )
    assert r.returncode == 0
    send_keys = [cmd for cmd in captured if "send-keys" in cmd]
    assert len(send_keys) >= 2


def test_paste_uses_pane_id(monkeypatch, tmp_path):
    captured = []

    def fake_run(*args, **kwargs):
        cmd = args[0]
        captured.append(cmd)
        if cmd[1:3] == ["capture-pane", "-t"]:
            return _completed(cmd, stdout="OpenCode")
        if cmd[1:3] == ["list-panes", "-t"]:
            if "#{pane_current_command}" in cmd:
                return _completed(cmd, stdout="node\n")
            return _completed(cmd, stdout="%99\n")
        if len(cmd) > 1 and cmd[1].endswith("restart-opencode-tmux-generic.sh"):
            tf = Path(cmd[-1])
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(
                json.dumps(
                    {
                        "session_name": cmd[3],
                        "pane_id": "%99",
                        "attach_command": f"tmux attach -t {cmd[3]}",
                        "project_dir": cmd[4],
                        "opencode_session_id": cmd[5],
                    }
                ),
                encoding="utf-8",
            )
            return _completed(cmd)
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.session_continuer.time.sleep", lambda _: None)
    c = OpencodeSessionContinuer(
        project_dir=tmp_path, tmux_target_path=tmp_path / ".omo_tmux_target.json"
    )
    r = c.continue_task(
        "ses-1",
        make_task(
            "t1", ExecutionMode.ONE_SHOT, "hello", project_path=str(tmp_path.resolve())
        ),
    )
    assert r.returncode == 0
    send_keys_calls = [cmd for cmd in captured if "send-keys" in cmd and "-t" in cmd]
    assert len(send_keys_calls) >= 2, (
        f"Expected at least 2 send-keys calls, got {len(send_keys_calls)}"
    )
    for sk in send_keys_calls:
        t_idx = sk.index("-t")
        target = sk[t_idx + 1]
        assert target == "%99", f"Expected send-keys -t %99, got -t {target}"


def test_pane_ready_uses_pane_id(monkeypatch, tmp_path):
    captured = []

    def fake_run(*args, **kwargs):
        cmd = args[0]
        captured.append(cmd)
        if cmd[1:3] == ["capture-pane", "-t"]:
            return _completed(cmd, stdout="OpenCode")
        if cmd[1:3] == ["list-panes", "-t"]:
            if "#{pane_current_command}" in cmd:
                return _completed(cmd, stdout="node\n")
            return _completed(cmd, stdout="%42\n")
        if len(cmd) > 1 and cmd[1].endswith("restart-opencode-tmux-generic.sh"):
            tf = Path(cmd[-1])
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(
                json.dumps(
                    {
                        "session_name": cmd[3],
                        "pane_id": "%42",
                        "attach_command": f"tmux attach -t {cmd[3]}",
                        "project_dir": cmd[4],
                        "opencode_session_id": cmd[5],
                    }
                ),
                encoding="utf-8",
            )
            return _completed(cmd)
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.session_continuer.time.sleep", lambda _: None)
    c = OpencodeSessionContinuer(
        project_dir=tmp_path, tmux_target_path=tmp_path / ".omo_tmux_target.json"
    )
    r = c.continue_task(
        "ses-1",
        make_task(
            "t1", ExecutionMode.ONE_SHOT, "hello", project_path=str(tmp_path.resolve())
        ),
    )
    assert r.returncode == 0
    # Verify capture-pane targets the pane ID
    capture_panes = [cmd for cmd in captured if "capture-pane" in cmd]
    for cp in capture_panes:
        assert "-t" in cp
        t_idx = cp.index("-t")
        target = cp[t_idx + 1]
        assert target == "%42", f"Expected capture-pane -t %42, got -t {target}"


def test_post_send_validation_failure_detected(monkeypatch, tmp_path):
    """After sending, if the pane becomes unresponsive, the task should fail."""
    ready_checks = {"count": 0}

    def fake_run(*args, **kwargs):
        cmd = args[0]
        if cmd[1:3] == ["capture-pane", "-t"]:
            ready_checks["count"] += 1
            if ready_checks["count"] <= 1:
                return _completed(cmd, stdout="OpenCode")
            return _completed(cmd, stdout="", stderr="pane not found")
        if cmd[1:3] == ["list-panes", "-t"]:
            if "#{pane_current_command}" in cmd:
                return _completed(cmd, stdout="node\n")
            target = cmd[3]
            if target.startswith("%"):
                return _completed(cmd, stdout="", stderr="pane not found")
            return _completed(cmd, stdout="%5\n")
        if len(cmd) > 1 and cmd[1].endswith("restart-opencode-tmux-generic.sh"):
            tf = Path(cmd[-1])
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(
                json.dumps(
                    {
                        "session_name": cmd[3],
                        "pane_id": "%5",
                        "attach_command": f"tmux attach -t {cmd[3]}",
                        "project_dir": cmd[4],
                        "opencode_session_id": cmd[5],
                    }
                ),
                encoding="utf-8",
            )
            return _completed(cmd)
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.session_continuer.time.sleep", lambda _: None)
    c = OpencodeSessionContinuer(
        project_dir=tmp_path, tmux_target_path=tmp_path / ".omo_tmux_target.json"
    )
    r = c.continue_task(
        "ses-1",
        make_task(
            "t1", ExecutionMode.ONE_SHOT, "hello", project_path=str(tmp_path.resolve())
        ),
    )
    assert r.returncode != 0
    assert "disappeared" in r.stderr


def test_active_pane_switch_does_not_affect_paste(monkeypatch, tmp_path):
    """Paste targets the live pane resolved at dispatch time, not the stale stored one."""
    pasted_to = {"target": None}

    def fake_run(*args, **kwargs):
        cmd = args[0]
        if cmd[1:3] == ["capture-pane", "-t"]:
            return _completed(cmd, stdout="OpenCode")
        if len(cmd) >= 3 and cmd[1:3] == ["send-keys", "-t"]:
            pasted_to["target"] = cmd[3]
            return _completed(cmd)
        if cmd[1:3] == ["list-panes", "-t"]:
            if "#{pane_current_command}" in cmd:
                return _completed(cmd, stdout="node\n")
            return _completed(cmd, stdout="%77\n%99\n")
        if len(cmd) > 1 and cmd[1].endswith("restart-opencode-tmux-generic.sh"):
            tf = Path(cmd[-1])
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(
                json.dumps(
                    {
                        "session_name": cmd[3],
                        "pane_id": "%99",
                        "attach_command": f"tmux attach -t {cmd[3]}",
                        "project_dir": cmd[4],
                        "opencode_session_id": cmd[5],
                    }
                ),
                encoding="utf-8",
            )
            return _completed(cmd)
        return _completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("omo_task_queue.session_continuer.time.sleep", lambda _: None)
    c = OpencodeSessionContinuer(
        project_dir=tmp_path, tmux_target_path=tmp_path / ".omo_tmux_target.json"
    )
    r = c.continue_task(
        "ses-1",
        make_task(
            "t1", ExecutionMode.ONE_SHOT, "hello", project_path=str(tmp_path.resolve())
        ),
    )
    assert r.returncode == 0
    assert pasted_to["target"] == "%77", (
        f"Expected paste to %77 (live pane), got {pasted_to['target']}"
    )


def test_build_prompt_preserves_marker():
    assert (
        OpencodeSessionContinuer._build_prompt(
            make_task("t1", ExecutionMode.ONE_SHOT, "hello/n")
        )
        == "hello/n"
    )


def test_build_prompt_strips_space_before_marker():
    assert (
        OpencodeSessionContinuer._build_prompt(
            make_task("t1", ExecutionMode.ONE_SHOT, "hello /n")
        )
        == "hello/n"
    )
