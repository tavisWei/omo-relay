from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from omo_task_queue.state import ExecutionMode, Task
from omo_task_queue.tmux_target import TmuxTarget, TmuxTargetStore, tmux_environment


@dataclass
class ContinuationState:
    task_id: str
    session_id: str
    baseline_message_id: str | None
    launched_at_ms: int


class ContinuationStateStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> ContinuationState | None:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return ContinuationState(**data)

    def save(self, state: ContinuationState) -> None:
        self._path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


class OpencodeSessionContinuer:
    def __init__(
        self,
        *,
        project_dir: str | Path,
        tmux_target_path: str | Path,
        tmux_executable: str | Path | None = None,
    ) -> None:
        self._project_dir = str(Path(project_dir).resolve())
        self._target_store = TmuxTargetStore(
            tmux_target_path,
            tmux_executable=tmux_executable,
        )
        self._tmux_executable = str(
            tmux_executable or (Path.home() / ".local" / "bin" / "tmux")
        )

    def continue_task(
        self, session_id: str, task: Task
    ) -> subprocess.CompletedProcess[str]:
        target_result = self.ensure_task_target(session_id, task)
        if isinstance(target_result, subprocess.CompletedProcess):
            return target_result
        target = target_result

        prompt = self._build_prompt(task)
        text_result = subprocess.run(
            [
                self._tmux_executable,
                "send-keys",
                "-t",
                target.pane_id,
                prompt,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=tmux_environment(),
        )
        if text_result.returncode != 0:
            return text_result
        return subprocess.run(
            [
                self._tmux_executable,
                "send-keys",
                "-t",
                target.pane_id,
                "Enter",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=tmux_environment(),
        )

    def ensure_task_target(
        self, session_id: str, task: Task
    ) -> TmuxTarget | subprocess.CompletedProcess[str]:
        expected_project_dir = task.project_path or self._project_dir
        expected_session_id = task.target_session_id or session_id
        target = self._target_store.load()
        if target is not None:
            valid, reason = self._target_store.validate_target(
                expected_project_dir=expected_project_dir,
                expected_session_id=expected_session_id,
            )
            recoverable_reasons = {
                "tmux target metadata missing",
                "tmux session not running",
                "tmux pane missing",
                "tmux target session mismatch",
                "tmux target project mismatch",
            }
            if (
                not valid
                and reason not in recoverable_reasons
                and not reason.startswith("tmux pane command mismatch:")
            ):
                return subprocess.CompletedProcess(
                    [self._tmux_executable],
                    1,
                    stdout="",
                    stderr=reason,
                )
        try:
            return self._target_store.ensure_target(
                project_dir=expected_project_dir,
                opencode_session_id=expected_session_id,
            )
        except RuntimeError as exc:
            return subprocess.CompletedProcess(
                [self._tmux_executable],
                1,
                stdout="",
                stderr=str(exc),
            )

    @staticmethod
    def _build_prompt(task: Task) -> str:
        prompt = OpencodeSessionContinuer._normalize_prompt(task.prompt)
        if task.mode is ExecutionMode.ONE_SHOT:
            return prompt
        command = "ulw-loop" if task.mode is ExecutionMode.ULW_LOOP else "ralph-loop"
        return f"/{command} {prompt}".strip()

    @staticmethod
    def _normalize_prompt(prompt: str) -> str:
        normalized = prompt.replace("\\n", "\n")
        normalized = normalized.rstrip()
        normalized = re.sub(r"\s+/n$", "/n", normalized)
        return normalized
