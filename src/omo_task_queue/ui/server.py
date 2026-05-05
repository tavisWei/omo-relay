from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from omo_task_queue.notifier import EmailNotifier, MockNotifier
from omo_task_queue.project_registry import ProjectRegistry
from omo_task_queue.retry import RetryManager
from omo_task_queue.confirmed_session import (
    ConfirmedSession,
    ConfirmedSessionStore,
    resolve_confirmed_session_id,
)
from omo_task_queue.session_selection import SessionSelection, SessionSelectionStore
from omo_task_queue.session_selection import ProjectSessionService
from omo_task_queue.session_continuer import ContinuationStateStore
from omo_task_queue.state import ExecutionMode, StateMachine, Task, TaskStatus
from omo_task_queue.notifier import NotificationConfig
from omo_task_queue.store import Config, SQLiteStore
from omo_task_queue.watcher_status import WatcherStatusStore
from omo_task_queue.ui.panel import (
    AddTaskRequest,
    PanelHandler,
    ReorderRequest,
    TaskActionRequest,
    TestNotificationRequest,
    UIAction,
)

logger = logging.getLogger("omo_task_queue.server")

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765


def _json_response(data: Any, status: int = 200) -> bytes:
    body = json.dumps(data, default=_json_default)
    return body.encode("utf-8")


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        return {k: getattr(obj, k) for k in obj.__dataclass_fields__}
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _parse_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


class QueueAPIHandler(BaseHTTPRequestHandler):
    panel: PanelHandler
    static_dir: Optional[Path] = None
    notification_config: NotificationConfig
    config_path: Optional[Path] = None
    status_provider: Optional[Callable[[], dict[str, Any]]] = None
    session_service: Optional[ProjectSessionService] = None
    project_registry: Optional[ProjectRegistry] = None

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_json(self, data: Any, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(_json_response(data, status))

    def _send_static(self, file_path: Path, status: int = 200) -> None:
        self.send_response(status)
        if file_path.suffix == ".html":
            self.send_header("Content-Type", "text/html")
        elif file_path.suffix == ".js":
            self.send_header("Content-Type", "application/javascript")
        elif file_path.suffix == ".css":
            self.send_header("Content-Type", "text/css")
        else:
            self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def _send_404(self) -> None:
        self._send_json({"error": "Not found"}, 404)

    def _resolve_project_session_id(
        self, project_path: str, preferred_session_id: str = ""
    ) -> str:
        from omo_task_queue.opencode_observer import OpenCodeObserver

        observer = OpenCodeObserver(
            Path.home() / ".local" / "share" / "opencode" / "opencode.db",
            project_path,
        )
        preferred = preferred_session_id.strip()
        if preferred and observer.session_belongs_to_project(preferred):
            return preferred
        if preferred:
            return preferred
        return observer.locate_primary_session() or ""

    def _persist_project_session_confirmation(
        self, project_path: str, session_id: str
    ) -> None:
        if not session_id:
            return
        project_dir = Path(project_path)
        previous_confirmed = ConfirmedSessionStore(project_dir).load()
        previous_session_id = (
            previous_confirmed.session_id if previous_confirmed is not None else None
        )
        SessionSelectionStore(project_dir / ".omo_selected_session.json").save(
            SessionSelection(session_id=session_id)
        )
        short_id = ConfirmedSessionStore.session_short_id(session_id)
        ConfirmedSessionStore(project_dir).save(
            ConfirmedSession(
                session_id=session_id,
                session_short_id=short_id,
                project_dir=str(project_dir.resolve()),
            )
        )
        if previous_session_id and previous_session_id != session_id:
            for task in self.panel._store.list_tasks(
                project_path=str(project_dir.resolve())
            ):
                if (
                    task.status is TaskStatus.RUNNING
                    and task.target_session_id == previous_session_id
                ):
                    StateMachine.transition(task, TaskStatus.RETRY_WAIT)
                    task.target_session_id = session_id
                    task.error_message = "Continuation session changed"
                    self.panel._store.update_task(task)
                    continue
                if (
                    task.status in {TaskStatus.PENDING, TaskStatus.RETRY_WAIT}
                    and task.target_session_id == previous_session_id
                ):
                    task.target_session_id = session_id
                    self.panel._store.update_task(task)
            ContinuationStateStore(
                project_dir / ".omo_session_watch_state.json"
            ).clear()
            status_path = project_dir / ".omo_watcher_status.json"
            if status_path.exists():
                status_path.unlink()

    def _start_project_server(
        self, project_path: str, session_id: str = ""
    ) -> dict[str, Any]:
        if not project_path:
            return {"success": False, "error": "Project path is required"}

        project_dir = Path(project_path)
        if not project_dir.exists():
            return {
                "success": False,
                "error": f"Project directory does not exist: {project_path}",
            }

        resolved_session_id = self._resolve_project_session_id(project_path, session_id)

        if self.project_registry:
            projects = self.project_registry.list_projects()
            for p in projects:
                if p.project_path == project_path and p.api_base_url:
                    import urllib.request

                    try:
                        urllib.request.urlopen(
                            p.api_base_url + "/api/status", timeout=2
                        )
                        self._start_project_watcher(project_path)
                        if resolved_session_id:
                            self._persist_project_session_confirmation(
                                project_path, resolved_session_id
                            )
                            tmux_result = self._ensure_project_tmux(
                                project_path, resolved_session_id
                            )
                            if not tmux_result["success"]:
                                logger.warning(
                                    "Tmux ensure failed for %s: %s",
                                    project_path,
                                    tmux_result.get("error"),
                                )
                        return {
                            "success": True,
                            "api_base_url": p.api_base_url,
                            "message": "Server already running",
                        }
                    except Exception:
                        pass

        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        cmd = [
            sys.executable,
            "-m",
            "omo_task_queue.serve",
            "--directory",
            str(project_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(project_path),
            )

            import time

            time.sleep(2)

            if process.poll() is not None:
                stdout, stderr = process.communicate()
                return {
                    "success": False,
                    "error": f"Server failed to start. stdout: {stdout.decode()}, stderr: {stderr.decode()}",
                }

            api_base_url = f"http://127.0.0.1:{port}"
            if self.project_registry:
                self.project_registry.upsert(
                    project_path=project_path,
                    api_base_url=api_base_url,
                )

            self._start_project_watcher(project_path)

            if resolved_session_id:
                self._persist_project_session_confirmation(
                    project_path, resolved_session_id
                )
                tmux_result = self._ensure_project_tmux(
                    project_path, resolved_session_id
                )
                if not tmux_result["success"]:
                    logger.warning(
                        "Tmux ensure failed for %s: %s",
                        project_path,
                        tmux_result.get("error"),
                    )

            return {
                "success": True,
                "api_base_url": api_base_url,
                "message": "Server started successfully",
            }

        except Exception as e:
            return {"success": False, "error": f"Failed to start server: {str(e)}"}

    def _start_project_watcher(self, project_path: str) -> None:
        self._kill_existing_watcher(project_path)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent.parent)
        watcher_cmd = [
            sys.executable,
            "-m",
            "omo_task_queue.watch",
            "--directory",
            str(project_path),
            "--poll-interval",
            "5",
            "--log-level",
            "INFO",
        ]
        try:
            subprocess.Popen(
                watcher_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(project_path),
                env=env,
            )
            logger.info("Started watcher for project: %s", project_path)
        except Exception as e:
            logger.error("Failed to start watcher for %s: %s", project_path, e)

    @staticmethod
    def _kill_existing_watcher(project_path: str) -> None:
        marker = f"omo_task_queue.watch.*--directory {project_path}"
        try:
            result = subprocess.run(
                ["pkill", "-f", marker],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                logger.info("Killed existing watchers for project: %s", project_path)
        except FileNotFoundError:
            pass

    def _ensure_project_tmux(
        self, project_path: str, session_id: str
    ) -> dict[str, Any]:
        from omo_task_queue.tmux_target import TmuxTargetStore
        from omo_task_queue.confirmed_session import ConfirmedSessionStore

        project_dir = Path(project_path)
        short_id = ConfirmedSessionStore.session_short_id(session_id)
        tmux_store = TmuxTargetStore(project_dir / f".omo_tmux_target.{short_id}.json")

        try:
            target = tmux_store.ensure_target(
                project_dir=project_dir,
                opencode_session_id=session_id,
            )
            return {
                "success": True,
                "tmux_session_name": target.session_name,
                "tmux_pane_id": target.pane_id,
                "attach_command": target.attach_command,
            }
        except RuntimeError as exc:
            error_msg = str(exc)
            if (
                "failed to create tmux target" in error_msg
                or "tmux" in error_msg.lower()
            ):
                logger.error("Tmux creation failed for %s: %s", project_path, error_msg)
            return {"success": False, "error": error_msg}

    def _forward_task_to_project(
        self, payload: dict[str, Any], target_project: str
    ) -> dict[str, Any]:
        if not self.project_registry:
            return {"success": False, "error": "Project registry unavailable"}
        projects = self.project_registry.list_projects()
        target = next(
            (
                p
                for p in projects
                if p.project_path == target_project and p.api_base_url
            ),
            None,
        )
        if target is None:
            return {
                "success": False,
                "error": f"Target project not running: {target_project}",
            }
        try:
            import urllib.request

            req = urllib.request.Request(
                f"{target.api_base_url}/api/tasks",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return {"success": False, "error": f"Forward failed: {exc}"}

    def _default_status(self) -> dict[str, Any]:
        counts = {
            "pending": 0,
            "running": 0,
            "retry_wait": 0,
            "done": 0,
            "skipped": 0,
        }
        for task in self.panel._store.list_tasks(project_path=self.panel._project_path):
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        return {
            "project_path": None,
            "counts": counts,
            "primary_session_id": None,
            "selected_session_id": None,
            "confirmed_session_id": None,
            "watcher_running": False,
            "watcher_decision": None,
            "watcher_reason": None,
            "watcher_last_checked_at": None,
            "active_continuation_task_id": None,
            "running_task_id": None,
            "pending_task_id": None,
            "latest_message_id": None,
            "latest_message_role": None,
            "latest_message_completed_ms": None,
            "latest_message_completed_at": None,
            "latest_activity_ms": None,
            "latest_activity_at": None,
            "is_quiet": False,
            "ready_for_continuation": False,
            "soft_stalled": False,
            "stalled": False,
            "idle_threshold": None,
            "soft_stalled_threshold": None,
            "stalled_threshold": None,
            "last_error": None,
        }

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _persist_notification_config(self) -> None:
        if self.config_path is None:
            return
        config = Config.load(self.config_path)
        config.notification_settings = asdict(self.notification_config)
        config.save(self.config_path)

    def _update_notification_config(self, payload: dict[str, Any]) -> None:
        current = asdict(self.notification_config)
        current.update(payload)
        self.notification_config = NotificationConfig(**current)
        type(self).notification_config = self.notification_config

        notifier = getattr(self.panel, "_notifier", None)
        if notifier is not None and hasattr(notifier, "config"):
            notifier.config = self.notification_config

        self._persist_notification_config()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        if path == "/api/queue":
            resp = self.panel.handle(UIAction.LIST_QUEUE)
            self._send_json({"success": resp.success, "data": resp.data})
            return

        if path in {"/api/running", "/api/queue/running"}:
            resp = self.panel.handle(UIAction.GET_RUNNING)
            self._send_json({"success": resp.success, "data": resp.data})
            return

        if path == "/api/notify/config":
            self._send_json({"success": True, "data": asdict(self.notification_config)})
            return

        if path == "/api/status":
            data = (
                self.status_provider()
                if self.status_provider
                else self._default_status()
            )
            self._send_json({"success": True, "data": data})
            return

        if path == "/api/projects":
            if self.project_registry:
                newly_registered = self.project_registry.auto_register_discovered()
                if newly_registered:
                    logger.info(
                        "Auto-registered %d new projects: %s",
                        len(newly_registered),
                        [p.project_name for p in newly_registered],
                    )
                projects = [
                    asdict(project) for project in self.project_registry.list_projects()
                ]
            else:
                projects = []
            self._send_json({"success": True, "data": projects})
            return

        if path == "/api/projects/start":
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            payload = _parse_json(body)
            project_path = payload.get("project_path", "")
            session_id = payload.get("session_id", "")
            result = self._start_project_server(project_path, session_id)
            self._send_json(result)
            return

        if path == "/api/sessions":
            sessions = (
                self.session_service.list_sessions() if self.session_service else []
            )
            selected = (
                self.session_service.get_selected_session_id()
                if self.session_service
                else None
            )
            self._send_json(
                {
                    "success": True,
                    "data": {
                        "selected_session_id": selected,
                        "sessions": [asdict(session) for session in sessions],
                    },
                }
            )
            return

        if self.static_dir and path == "/":
            index = self.static_dir / "index.html"
            if index.exists():
                self._send_static(index)
                return

        if self.static_dir:
            target = self.static_dir / path.lstrip("/")
            if target.exists() and target.is_file():
                self._send_static(target)
                return

        self._send_404()

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        payload = _parse_json(body)

        if path == "/api/projects/start":
            project_path = payload.get("project_path", "")
            session_id = payload.get("session_id", "")
            result = self._start_project_server(project_path, session_id)
            self._send_json(result)
            return

        if path in {"/api/tasks", "/api/queue"}:
            target_project = payload.get("target_project", "")
            if target_project and target_project != self.panel._project_path:
                result = self._forward_task_to_project(payload, target_project)
                self._send_json(result)
                return
            req = AddTaskRequest(
                title=payload.get("title", ""),
                prompt=payload.get("prompt", ""),
                mode=ExecutionMode(payload.get("mode", "one_shot")),
                max_retries=payload.get("max_retries", 3),
            )
            resp = self.panel.handle(UIAction.ADD_TASK, req)
            self._send_json(
                {"success": resp.success, "data": resp.data, "error": resp.error}
            )
            return

        if path in {"/api/tasks/reorder", "/api/queue/reorder"}:
            req = ReorderRequest(
                task_id=payload.get("task_id", ""),
                new_order=payload.get("new_order", 0),
            )
            resp = self.panel.handle(UIAction.REORDER, req)
            self._send_json({"success": resp.success, "error": resp.error})
            return

        parts = path.split("/")
        if len(parts) == 5 and parts[1] == "api" and parts[2] in {"tasks", "queue"}:
            action = parts[4]
            task_id = parts[3]
            if action == "delete":
                resp = self.panel.handle(
                    UIAction.DELETE, TaskActionRequest(task_id=task_id)
                )
                self._send_json({"success": resp.success, "error": resp.error})
                return
            if action == "skip":
                resp = self.panel.handle(
                    UIAction.SKIP, TaskActionRequest(task_id=task_id)
                )
                self._send_json({"success": resp.success, "error": resp.error})
                return
            if action == "done":
                resp = self.panel.handle(
                    UIAction.DONE, TaskActionRequest(task_id=task_id)
                )
                self._send_json({"success": resp.success, "error": resp.error})
                return
            if action == "retry":
                resp = self.panel.handle(
                    UIAction.RETRY, TaskActionRequest(task_id=task_id)
                )
                self._send_json({"success": resp.success, "error": resp.error})
                return

        if path in {"/api/notifications/test", "/api/notify/test"}:
            req = TestNotificationRequest(recipient=payload.get("recipient"))
            resp = self.panel.handle(UIAction.TEST_NOTIFICATION, req)
            self._send_json({"success": resp.success, "error": resp.error})
            return

        if path == "/api/notify/config":
            self._update_notification_config(payload)
            self._send_json({"success": True, "data": asdict(self.notification_config)})
            return

        if path == "/api/sessions/select":
            if self.session_service is None:
                self._send_json(
                    {"success": False, "error": "Session service unavailable"}, 400
                )
                return
            session_id = str(payload.get("session_id", "")).strip()
            try:
                selected = self.session_service.select_session(session_id)
            except ValueError as exc:
                self._send_json({"success": False, "error": str(exc)}, 400)
                return
            self._send_json(
                {"success": True, "data": {"selected_session_id": selected}}
            )
            return

        if path == "/api/sessions/confirm":
            if self.session_service is None:
                self._send_json(
                    {"success": False, "error": "Session service unavailable"}, 400
                )
                return
            session_id = str(payload.get("session_id", "")).strip()
            if not session_id:
                self._send_json(
                    {"success": False, "error": "session_id is required"}, 400
                )
                return
            try:
                self.session_service.select_session(session_id)
            except ValueError as exc:
                self._send_json({"success": False, "error": str(exc)}, 400)
                return
            self._persist_project_session_confirmation(
                self.panel._project_path, session_id
            )
            tmux_result = self._ensure_project_tmux(
                self.panel._project_path, session_id
            )
            if not tmux_result["success"]:
                logger.warning(
                    "Tmux ensure failed for session confirm %s: %s",
                    session_id,
                    tmux_result.get("error"),
                )
                self._send_json(
                    {
                        "success": False,
                        "error": tmux_result.get("error", "tmux ensure failed"),
                    },
                    500,
                )
                return
            self._send_json(
                {
                    "success": True,
                    "data": {
                        "confirmed_session_id": session_id,
                        "tmux_session_name": tmux_result.get("tmux_session_name"),
                    },
                }
            )
            return

        self._send_404()

    def do_DELETE(self) -> None:
        path = self.path.split("?")[0]
        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "api" and parts[2] == "queue":
            task_id = parts[3]
            resp = self.panel.handle(
                UIAction.DELETE, TaskActionRequest(task_id=task_id)
            )
            self._send_json({"success": resp.success, "error": resp.error})
            return

        self._send_404()


def make_handler(
    panel: PanelHandler,
    static_dir: Optional[Path] = None,
    notification_config: Optional[NotificationConfig] = None,
    config_path: Optional[Path] = None,
    status_provider: Optional[Callable[[], dict[str, Any]]] = None,
    session_service: Optional[ProjectSessionService] = None,
    project_registry: Optional[ProjectRegistry] = None,
) -> type[BaseHTTPRequestHandler]:
    class BoundHandler(QueueAPIHandler):
        pass

    BoundHandler.panel = panel
    BoundHandler.static_dir = static_dir
    BoundHandler.notification_config = notification_config or NotificationConfig()
    BoundHandler.config_path = config_path
    BoundHandler.status_provider = (
        staticmethod(status_provider) if status_provider else None
    )
    BoundHandler.session_service = session_service
    BoundHandler.project_registry = project_registry
    return BoundHandler


def create_server(
    store: SQLiteStore,
    notifier: Any = None,
    queue_starter: Any = None,
    tmux_target_store: Any = None,
    project_path: str = "",
    session_resolver: Optional[Callable[[], Optional[str]]] = None,
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    static_dir: Optional[str | Path] = None,
    config_path: Optional[str | Path] = None,
    status_provider: Optional[Callable[[], dict[str, Any]]] = None,
    session_service: Optional[ProjectSessionService] = None,
    project_registry: Optional[ProjectRegistry] = None,
) -> HTTPServer:
    panel = PanelHandler(
        store,
        notifier=notifier,
        queue_starter=queue_starter,
        tmux_target_store=tmux_target_store,
        project_path=project_path,
        session_resolver=session_resolver,
    )
    loaded_config = Config.load(Path(config_path)) if config_path else Config()
    notification_config = getattr(notifier, "config", None) or NotificationConfig(
        **loaded_config.notification_settings
    )
    handler_cls = make_handler(
        panel,
        static_dir=Path(static_dir) if static_dir else None,
        notification_config=notification_config,
        config_path=Path(config_path) if config_path else None,
        status_provider=status_provider,
        session_service=session_service,
        project_registry=project_registry,
    )
    return HTTPServer((host, port), handler_cls)


def run_server(
    store: SQLiteStore,
    notifier: Any = None,
    queue_starter: Any = None,
    tmux_target_store: Any = None,
    project_path: str = "",
    session_resolver: Optional[Callable[[], Optional[str]]] = None,
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    static_dir: Optional[str | Path] = None,
    config_path: Optional[str | Path] = None,
    status_provider: Optional[Callable[[], dict[str, Any]]] = None,
    session_service: Optional[ProjectSessionService] = None,
    project_registry: Optional[ProjectRegistry] = None,
) -> None:
    server = create_server(
        store=store,
        notifier=notifier,
        queue_starter=queue_starter,
        tmux_target_store=tmux_target_store,
        project_path=project_path,
        session_resolver=session_resolver,
        host=host,
        port=port,
        static_dir=static_dir,
        config_path=config_path,
        status_provider=status_provider,
        session_service=session_service,
        project_registry=project_registry,
    )
    print(f"OMO Task Queue server running on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.shutdown()
