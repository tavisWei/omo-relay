from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ProjectRegistryEntry:
    project_path: str
    project_name: str
    api_base_url: str
    updated_at: str


class ProjectRegistry:
    def __init__(
        self, path: str | Path, opencode_db_path: str | Path | None = None
    ) -> None:
        self._path = Path(path)
        self._opencode_db_path = Path(opencode_db_path) if opencode_db_path else None

    def list_projects(self) -> list[ProjectRegistryEntry]:
        registered = self._load_registered()
        discovered = self._discover_from_opencode()

        registered_paths = {entry.project_path for entry in registered}
        merged = list(registered)

        for disc_entry in discovered:
            if disc_entry.project_path not in registered_paths:
                merged.append(disc_entry)

        merged.sort(key=lambda item: item.project_name.lower())
        return merged

    def auto_register_discovered(self) -> list[ProjectRegistryEntry]:
        registered = self._load_registered()
        discovered = self._discover_from_opencode()

        registered_paths = {entry.project_path for entry in registered}
        newly_registered = []

        for disc_entry in discovered:
            if disc_entry.project_path not in registered_paths:
                registered.append(disc_entry)
                newly_registered.append(disc_entry)

        if newly_registered:
            registered.sort(key=lambda item: item.project_name.lower())
            self._path.write_text(
                json.dumps(
                    [asdict(entry) for entry in registered], indent=2, sort_keys=True
                ),
                encoding="utf-8",
            )

        return newly_registered

    def _load_registered(self) -> list[ProjectRegistryEntry]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return [ProjectRegistryEntry(**item) for item in data]

    def _discover_from_opencode(self) -> list[ProjectRegistryEntry]:
        if self._opencode_db_path is None or not self._opencode_db_path.exists():
            return []

        try:
            with sqlite3.connect(str(self._opencode_db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT directory 
                    FROM session 
                    WHERE parent_id IS NULL 
                    ORDER BY directory
                    """
                ).fetchall()
        except (sqlite3.Error, OSError):
            return []

        discovered = []
        for row in rows:
            project_path = str(Path(row[0]).resolve())
            project_name = Path(project_path).name or project_path
            discovered.append(
                ProjectRegistryEntry(
                    project_path=project_path,
                    project_name=project_name,
                    api_base_url="",
                    updated_at=datetime.utcnow().isoformat(),
                )
            )

        return discovered

    def upsert(self, *, project_path: str | Path, api_base_url: str) -> None:
        project_path = str(Path(project_path).resolve())
        entries = self._load_registered()
        updated = ProjectRegistryEntry(
            project_path=project_path,
            project_name=Path(project_path).name or project_path,
            api_base_url=api_base_url,
            updated_at=datetime.utcnow().isoformat(),
        )
        replaced = False
        for index, entry in enumerate(entries):
            if entry.project_path == project_path:
                entries[index] = updated
                replaced = True
                break
        if not replaced:
            entries.append(updated)
        entries.sort(key=lambda item: item.project_name.lower())
        self._path.write_text(
            json.dumps([asdict(entry) for entry in entries], indent=2, sort_keys=True),
            encoding="utf-8",
        )
