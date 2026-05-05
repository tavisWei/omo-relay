from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from omo_task_queue.confirmed_session import ConfirmedSessionStore
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
        self._tmux_target_path = Path(tmux_target_path)
        self._tmux_executable = str(
            tmux_executable or (Path.home() / ".local" / "bin" / "tmux")
        )
        self._target_store = self._get_target_store("")

    def _get_target_store(self, session_id: str) -> TmuxTargetStore:
        if session_id:
            short_id = ConfirmedSessionStore.session_short_id(session_id)
            stem = self._tmux_target_path.stem
            suffix = self._tmux_target_path.suffix
            path = self._tmux_target_path.with_name(f"{stem}.{short_id}{suffix}")
        else:
            path = self._tmux_target_path
        return TmuxTargetStore(path, tmux_executable=self._tmux_executable)

    def continue_task(
        self, session_id: str, task: Task
    ) -> subprocess.CompletedProcess[str]:
        target_store = self._get_target_store(task.target_session_id or session_id)
        target_result = self.ensure_task_target(
            session_id, task, target_store=target_store
        )
        if isinstance(target_result, subprocess.CompletedProcess):
            return target_result
        target = target_result

        prompt = self._build_prompt(task)

        if not self._is_pane_ready(target.session_name):
            for _ in range(30):
                time.sleep(1)
                if self._is_pane_ready(target.session_name):
                    break
            time.sleep(15)

        result = self._send_buffer(target.session_name, prompt)
        if result.returncode != 0:
            return result
        return self._send_buffer(target.session_name, "\n")

    def _is_pane_ready(self, session_name: str) -> bool:
        result = subprocess.run(
            [self._tmux_executable, "capture-pane", "-t", session_name, "-p"],
            capture_output=True,
            text=True,
            check=False,
            env=tmux_environment(),
        )
        return result.returncode == 0 and "OpenCode" in (result.stdout or "")

    def _send_buffer(
        self, session_name: str, text: str
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self._tmux_executable, "load-buffer", "-"],
            input=text.encode("utf-8"),
            capture_output=True,
            check=False,
            env=tmux_environment(),
        )
        if result.returncode != 0:
            return result
        return subprocess.run(
            [self._tmux_executable, "paste-buffer", "-t", session_name],
            capture_output=True,
            text=True,
            check=False,
            env=tmux_environment(),
        )

    def ensure_task_target(
        self,
        session_id: str,
        task: Task,
        *,
        target_store: TmuxTargetStore | None = None,
    ) -> TmuxTarget | subprocess.CompletedProcess[str]:
        expected_project_dir = task.project_path or self._project_dir
        expected_session_id = task.target_session_id or session_id
        store = target_store or self._get_target_store(expected_session_id)
        target = store.load()
        if target is not None:
            valid, reason = store.validate_target(
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
            return store.ensure_target(
                project_dir=expected_project_dir,
                opencode_session_id=expected_session_id,
            )
        except RuntimeError as exc:
            error_msg = str(exc)
            if "tmux target project mismatch" in error_msg:
                store.clear()
                try:
                    return store.ensure_target(
                        project_dir=expected_project_dir,
                        opencode_session_id=expected_session_id,
                    )
                except RuntimeError as retry_exc:
                    return subprocess.CompletedProcess(
                        [self._tmux_executable],
                        1,
                        stdout="",
                        stderr=str(retry_exc),
                    )
            return subprocess.CompletedProcess(
                [self._tmux_executable],
                1,
                stdout="",
                stderr=error_msg,
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
