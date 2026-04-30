from __future__ import annotations

import json
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
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list_projects(self) -> list[ProjectRegistryEntry]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return [ProjectRegistryEntry(**item) for item in data]

    def upsert(self, *, project_path: str | Path, api_base_url: str) -> None:
        project_path = str(Path(project_path).resolve())
        entries = self.list_projects()
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
