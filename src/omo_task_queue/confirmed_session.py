from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ConfirmedSession:
    session_id: str
    session_short_id: str
    project_dir: str


class ConfirmedSessionStore:
    def __init__(self, project_dir: str | Path) -> None:
        self._path = Path(project_dir) / ".omo_confirmed_session.json"

    def load(self) -> Optional[ConfirmedSession]:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return ConfirmedSession(**data)

    def save(self, session: ConfirmedSession) -> None:
        self._path.write_text(
            json.dumps(asdict(session), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()

    @staticmethod
    def session_short_id(session_id: str) -> str:
        return session_id.replace("ses_", "")[:8]


def resolve_confirmed_session_id(
    project_dir: str | Path, selected_session_id: str | None = None
) -> Optional[str]:
    project_path = Path(project_dir)
    store = ConfirmedSessionStore(project_path)
    confirmed = store.load()
    if confirmed is not None:
        return confirmed.session_id

    session_id = (selected_session_id or "").strip()
    if not session_id:
        from omo_task_queue.session_selection import SessionSelectionStore

        selected = SessionSelectionStore(
            project_path / ".omo_selected_session.json"
        ).load()
        session_id = selected.session_id if selected is not None else ""

    if not session_id:
        return None

    store.save(
        ConfirmedSession(
            session_id=session_id,
            session_short_id=ConfirmedSessionStore.session_short_id(session_id),
            project_dir=str(project_path.resolve()),
        )
    )
    return session_id
