from __future__ import annotations

import json
import os
import hashlib
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


def build_attach_command(tmux_executable: str | Path, session_name: str) -> str:
    return f"{Path(tmux_executable)} attach -t {session_name}"


def normalize_attach_command(
    attach_command: str | None,
    *,
    tmux_executable: str | Path,
    session_name: str,
) -> str:
    expected = build_attach_command(tmux_executable, session_name)
    legacy = f"tmux attach -t {session_name}"
    if not attach_command or attach_command == legacy:
        return expected
    return attach_command


def tmux_environment() -> dict[str, str]:
    env = dict(os.environ)
    local_bin = str(Path.home() / ".local" / "bin")
    local_lib = str(Path.home() / ".local" / "lib")
    env["PATH"] = f"{local_bin}:{env.get('PATH', '')}" if env.get("PATH") else local_bin
    current_dyld = env.get("DYLD_LIBRARY_PATH", "")
    env["DYLD_LIBRARY_PATH"] = (
        f"{local_lib}:{current_dyld}" if current_dyld else local_lib
    )
    return env


def resolve_opencode_executable() -> str:
    env = tmux_environment()
    resolved = shutil.which("opencode", path=env.get("PATH"))
    return resolved or "opencode"


def build_opencode_launch_command(opencode_session_id: str) -> str:
    local_bin = Path.home() / ".local" / "bin"
    local_lib = Path.home() / ".local" / "lib"
    opencode_executable = resolve_opencode_executable()
    return (
        f"export DYLD_LIBRARY_PATH='{local_lib}:$DYLD_LIBRARY_PATH'; "
        f"export PATH='{local_bin}:$PATH'; "
        f"exec {subprocess.list2cmdline([opencode_executable])} -s {subprocess.list2cmdline([opencode_session_id])} ."
    )


@dataclass
class TmuxTarget:
    session_name: str
    pane_id: str
    attach_command: str
    project_dir: str
    opencode_session_id: Optional[str] = None


class TmuxTargetStore:
    def __init__(
        self, path: str | Path, tmux_executable: str | Path | None = None
    ) -> None:
        self._path = Path(path)
        self._tmux_executable = str(
            tmux_executable or (Path.home() / ".local" / "bin" / "tmux")
        )

    def load(self) -> Optional[TmuxTarget]:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        data["attach_command"] = normalize_attach_command(
            data.get("attach_command"),
            tmux_executable=self._tmux_executable,
            session_name=data["session_name"],
        )
        return TmuxTarget(**data)

    def save(self, target: TmuxTarget) -> None:
        payload = asdict(target)
        payload["attach_command"] = normalize_attach_command(
            target.attach_command,
            tmux_executable=self._tmux_executable,
            session_name=target.session_name,
        )
        self._path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()

    def tmux_running(self) -> bool:
        target = self.load()
        if target is None:
            return False
        result = subprocess.run(
            [self._tmux_executable, "has-session", "-t", target.session_name],
            capture_output=True,
            text=True,
            check=False,
            env=tmux_environment(),
        )
        return result.returncode == 0

    def pane_exists(self) -> bool:
        target = self.load()
        if target is None:
            return False
        result = subprocess.run(
            [
                self._tmux_executable,
                "list-panes",
                "-t",
                target.session_name,
                "-F",
                "#{pane_id}",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=tmux_environment(),
        )
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            if line.strip() == target.pane_id:
                return True
        return False

    def pane_command(self) -> Optional[str]:
        target = self.load()
        if target is None:
            return None
        result = subprocess.run(
            [
                self._tmux_executable,
                "list-panes",
                "-t",
                target.session_name,
                "-F",
                "#{pane_id}|#{pane_current_command}",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=tmux_environment(),
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            pane_id, _, command = line.partition("|")
            if pane_id == target.pane_id:
                return command or None
        return None

    def validate_target(
        self,
        *,
        expected_project_dir: str | Path,
        expected_session_id: str,
    ) -> tuple[bool, str]:
        target = self.load()
        if target is None:
            return False, "tmux target metadata missing"
        if str(Path(target.project_dir).resolve()) != str(
            Path(expected_project_dir).resolve()
        ):
            return False, "tmux target project mismatch"
        if target.opencode_session_id != expected_session_id:
            return False, "tmux target session mismatch"
        if not self.tmux_running():
            return False, "tmux session not running"
        if not self.pane_exists():
            return False, "tmux pane missing"
        return True, "ok"

    def ensure_target(
        self,
        *,
        project_dir: str | Path,
        opencode_session_id: str,
    ) -> TmuxTarget:
        project_dir = str(Path(project_dir).resolve())
        existing = self.load()
        if existing is not None:
            valid, _ = self.validate_target(
                expected_project_dir=project_dir,
                expected_session_id=opencode_session_id,
            )
            if valid:
                return existing
            self.clear()

        project_hash = hashlib.sha256(project_dir.encode()).hexdigest()[:12]
        session_name = f"omo-{project_hash}"
        self._restart_target_session(
            session_name=session_name,
            project_dir=project_dir,
            opencode_session_id=opencode_session_id,
        )
        target = self.load()
        if target is None:
            raise RuntimeError("failed to create tmux target")
        valid, reason = self.validate_target(
            expected_project_dir=project_dir,
            expected_session_id=opencode_session_id,
        )
        if not valid:
            self.clear()
            raise RuntimeError(reason)
        return target

    def _restart_target_session(
        self,
        *,
        session_name: str,
        project_dir: str,
        opencode_session_id: str,
    ) -> None:
        script_path = Path(project_dir) / "scripts" / "restart-opencode-tmux.sh"
        if script_path.exists():
            env = tmux_environment()
            env["TMUX_BIN"] = self._tmux_executable
            env["OPENCODE_TMUX_SESSION"] = session_name
            result = subprocess.run(
                [str(script_path)],
                capture_output=True,
                text=True,
                check=False,
                env=env,
                cwd=project_dir,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "failed to create tmux target"
                )
            return

        subprocess.run(
            [self._tmux_executable, "kill-session", "-t", session_name],
            capture_output=True,
            text=True,
            check=False,
            env=tmux_environment(),
        )
        result = subprocess.run(
            [
                self._tmux_executable,
                "new-session",
                "-d",
                "-s",
                session_name,
                "-c",
                project_dir,
                build_opencode_launch_command(opencode_session_id),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=tmux_environment(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip()
                or result.stdout.strip()
                or "failed to create tmux target"
            )
        time.sleep(3)
        pane_id = self._wait_for_pane_id(session_name)
        self.save(
            TmuxTarget(
                session_name=session_name,
                pane_id=pane_id,
                attach_command=build_attach_command(
                    self._tmux_executable, session_name
                ),
                project_dir=project_dir,
                opencode_session_id=opencode_session_id,
            )
        )

    def _wait_for_pane_id(self, session_name: str, attempts: int = 20) -> str:
        for _ in range(attempts):
            result = subprocess.run(
                [
                    self._tmux_executable,
                    "list-panes",
                    "-t",
                    session_name,
                    "-F",
                    "#{pane_id}",
                ],
                capture_output=True,
                text=True,
                check=False,
                env=tmux_environment(),
            )
            if result.returncode == 0:
                pane_id = (
                    result.stdout.strip().splitlines()[0]
                    if result.stdout.strip()
                    else ""
                )
                if pane_id:
                    stable = subprocess.run(
                        [self._tmux_executable, "has-session", "-t", session_name],
                        capture_output=True,
                        text=True,
                        check=False,
                        env=tmux_environment(),
                    )
                    if stable.returncode == 0:
                        return pane_id
            time.sleep(0.25)
        raise RuntimeError("tmux pane not ready")
