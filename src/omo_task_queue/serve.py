from __future__ import annotations

import argparse
from pathlib import Path

from omo_task_queue.confirmed_session import resolve_confirmed_session_id
from omo_task_queue.notifier import EmailNotifier, MockNotifier, NotificationConfig
from omo_task_queue.opencode_observer import OpenCodeObserver
from omo_task_queue.project_registry import ProjectRegistry
from omo_task_queue.session_selection import (
    ProjectSessionService,
    SessionSelectionStore,
)
from omo_task_queue.status_provider import QueueStatusProvider
from omo_task_queue.store import Config, SQLiteStore
from omo_task_queue.tmux_target import TmuxTargetStore
from omo_task_queue.ui.server import run_server


def _default_static_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def _build_notifier(config: Config):
    settings = config.notification_settings
    if settings.get("enabled"):
        return EmailNotifier(
            NotificationConfig(
                enabled=True,
                smtp_host=settings.get("smtp_host", "localhost"),
                smtp_port=settings.get("smtp_port", 587),
                smtp_user=settings.get("smtp_user", ""),
                smtp_password=settings.get("smtp_password", ""),
                smtp_use_tls=settings.get("smtp_use_tls", True),
                smtp_use_ssl=settings.get("smtp_use_ssl", False),
                recipient=settings.get("recipient", ""),
                sender=settings.get("sender", ""),
            )
        )
    return MockNotifier()


def _api_base_url(host: str, port: int) -> str:
    resolved_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{resolved_host}:{port}"


def _session_resolver(base_dir: Path, session_service: ProjectSessionService):
    def resolve() -> str | None:
        selected = session_service.get_selected_session_id()
        return resolve_confirmed_session_id(base_dir, selected)

    return resolve


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OMO Task Queue backend server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--directory", default=".")
    parser.add_argument("--static-dir", default=None)
    args = parser.parse_args()

    base_dir = Path(args.directory).resolve()
    config_path = base_dir / "omo_task_queue.json"
    db_path = base_dir / "omo_task_queue.db"
    static_dir = (
        Path(args.static_dir).resolve() if args.static_dir else _default_static_dir()
    )

    config = Config.load(config_path)
    notifier = _build_notifier(config)
    store = SQLiteStore(db_path)
    tmux_target_store = TmuxTargetStore(base_dir / ".omo_tmux_target.json")
    opencode_db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    observer = (
        OpenCodeObserver(opencode_db_path, base_dir)
        if opencode_db_path.exists()
        else None
    )
    session_service = ProjectSessionService(
        observer,
        SessionSelectionStore(base_dir / ".omo_selected_session.json"),
    )
    project_registry = ProjectRegistry(
        Path.home() / ".omo_project_registry.json",
        opencode_db_path=opencode_db_path,
    )
    project_registry.upsert(
        project_path=base_dir,
        api_base_url=_api_base_url(args.host, args.port),
    )
    status_provider = QueueStatusProvider(
        store=store,
        config=config,
        project_path=base_dir,
        opencode_db_path=opencode_db_path,
        watcher_status_path=base_dir / ".omo_watcher_status.json",
        continuation_state_path=base_dir / ".omo_session_watch_state.json",
        session_service=session_service,
    )
    try:
        run_server(
            store=store,
            notifier=notifier,
            tmux_target_store=tmux_target_store,
            project_path=str(base_dir),
            session_resolver=_session_resolver(base_dir, session_service),
            host=args.host,
            port=args.port,
            static_dir=static_dir if static_dir.exists() else None,
            config_path=config_path,
            status_provider=status_provider.status,
            session_service=session_service,
            project_registry=project_registry,
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
