from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from omo_task_queue.notifier import EmailNotifier, MockNotifier
from omo_task_queue.project_registry import ProjectRegistry
from omo_task_queue.retry import RetryManager
from omo_task_queue.session_selection import ProjectSessionService
from omo_task_queue.state import ExecutionMode, StateMachine, Task, TaskStatus
from omo_task_queue.notifier import NotificationConfig
from omo_task_queue.store import Config, SQLiteStore
from omo_task_queue.ui.panel import (
    AddTaskRequest,
    PanelHandler,
    ReorderRequest,
    TaskActionRequest,
    TestNotificationRequest,
    UIAction,
)

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
            projects = (
                [asdict(project) for project in self.project_registry.list_projects()]
                if self.project_registry
                else []
            )
            self._send_json({"success": True, "data": projects})
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

        if path in {"/api/tasks", "/api/queue"}:
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
