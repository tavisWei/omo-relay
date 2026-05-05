from __future__ import annotations

import subprocess
import json
from pathlib import Path

from omo_task_queue.session_continuer import OpencodeSessionContinuer
from omo_task_queue.state import ExecutionMode, Task


def make_task(task_id, mode, prompt="Prompt", *, project_path="", target_session_id=None):
    return Task(id=task_id, title=f"Task {task_id}", prompt=prompt, mode=mode, project_path=project_path, target_session_id=target_session_id)


def _completed(cmd, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, 0, stdout, stderr)


def test_continue_task_pastes_prompt(monkeypatch, tmp_path):
    captured = []
    def fake_run(*args, **kwargs):
        cmd = args[0]
        captured.append(cmd)
        if cmd[1:3] == ["list-panes", "-t"]:
            if "#{pane_current_command}" in cmd:
                return _completed(cmd, stdout="node\n")
            return _completed(cmd, stdout="%1\n")
        if len(cmd) > 1 and cmd[1].endswith("restart-opencode-tmux-generic.sh"):
            tf = Path(cmd[-1])
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(json.dumps({"session_name": cmd[3], "pane_id": "%1", "attach_command": f"tmux attach -t {cmd[3]}", "project_dir": cmd[4], "opencode_session_id": cmd[5]}), encoding="utf-8")
            return _completed(cmd)
        return _completed(cmd)
    monkeypatch.setattr(subprocess, "run", fake_run)
    c = OpencodeSessionContinuer(project_dir=tmp_path, tmux_target_path=tmp_path / ".omo_tmux_target.json")
    r = c.continue_task("ses-1", make_task("t1", ExecutionMode.ONE_SHOT, "hello", project_path=str(tmp_path.resolve())))
    assert r.returncode == 0


def test_continue_task_warmup_when_pane_not_ready(monkeypatch, tmp_path):
    captured = []
    ready = [False]
    def fake_run(*args, **kwargs):
        cmd = args[0]
        captured.append(cmd)
        if cmd[1:3] == ["list-panes", "-t"]:
            if "#{pane_current_command}" in cmd:
                if ready[0]:
                    return _completed(cmd, stdout="node\n")
                ready[0] = True
                return _completed(cmd, stdout="bash\n")
            return _completed(cmd, stdout="%9\n")
        if len(cmd) > 1 and cmd[1].endswith("restart-opencode-tmux-generic.sh"):
            tf = Path(cmd[-1])
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(json.dumps({"session_name": cmd[3], "pane_id": "%9", "attach_command": f"tmux attach -t {cmd[3]}", "project_dir": cmd[4], "opencode_session_id": cmd[5]}), encoding="utf-8")
            return _completed(cmd)
        return _completed(cmd)
    monkeypatch.setattr(subprocess, "run", fake_run)
    c = OpencodeSessionContinuer(project_dir=tmp_path, tmux_target_path=tmp_path / ".omo_tmux_target.ses-2.json")
    r = c.continue_task("ses-2", make_task("t1", ExecutionMode.ONE_SHOT, "hello", project_path=str(tmp_path.resolve()), target_session_id="ses-2"))
    assert r.returncode == 0
    paste = [cmd for cmd in captured if "paste-buffer" in cmd]
    assert len(paste) >= 2


def test_build_prompt_preserves_marker():
    assert OpencodeSessionContinuer._build_prompt(make_task("t1", ExecutionMode.ONE_SHOT, "hello/n")) == "hello/n"


def test_build_prompt_strips_space_before_marker():
    assert OpencodeSessionContinuer._build_prompt(make_task("t1", ExecutionMode.ONE_SHOT, "hello /n")) == "hello/n"
