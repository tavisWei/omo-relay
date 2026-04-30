from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from omo_task_queue.opencode_observer import OpenCodeObserver, ProjectSession


@dataclass
class SessionSelection:
    session_id: str


class SessionSelectionStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> Optional[SessionSelection]:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return SessionSelection(**data)

    def save(self, selection: SessionSelection) -> None:
        self._path.write_text(
            json.dumps(asdict(selection), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


class ProjectSessionService:
    def __init__(
        self,
        observer: OpenCodeObserver | None,
        selection_store: SessionSelectionStore,
    ) -> None:
        self._observer = observer
        self._selection_store = selection_store

    def list_sessions(self) -> list[ProjectSession]:
        if self._observer is None:
            return []
        return self._observer.list_project_sessions()

    def get_selected_session_id(self) -> str | None:
        if self._observer is None:
            return None
        selection = self._selection_store.load()
        if selection and self._observer.session_belongs_to_project(
            selection.session_id
        ):
            return selection.session_id
        session_id = self._observer.locate_primary_session()
        if session_id is None:
            return None
        self._selection_store.save(SessionSelection(session_id=session_id))
        return session_id

    def select_session(self, session_id: str) -> str:
        if self._observer is None or not self._observer.session_belongs_to_project(
            session_id
        ):
            raise ValueError("Session does not belong to current project")
        self._selection_store.save(SessionSelection(session_id=session_id))
        return session_id
