"""OMO Task Queue Plugin — Persistence layer."""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from omo_task_queue.state import ExecutionMode, Task, TaskStatus


@dataclass
class Config:
    """Plugin configuration with persistence support.

    Attributes:
        idle_threshold: Seconds of inactivity before considering a loop complete.
        max_retries: Default maximum retry attempts for tasks.
        retry_backoff_seconds: Base delay between retry attempts.
        notification_settings: Dict with keys: enabled, smtp_host, smtp_port,
            smtp_user, smtp_password, smtp_use_tls, recipient, sender.
    """

    idle_threshold: int = 3
    soft_stalled_threshold: int = 3
    stalled_threshold: int = 3
    max_retries: int = 3
    retry_backoff_seconds: int = 5
    notification_settings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize config to a plain dictionary."""
        return {
            "idle_threshold": self.idle_threshold,
            "soft_stalled_threshold": self.soft_stalled_threshold,
            "stalled_threshold": self.stalled_threshold,
            "max_retries": self.max_retries,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "notification_settings": self.notification_settings,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Deserialize config from a plain dictionary."""
        return cls(
            idle_threshold=data.get("idle_threshold", 3),
            soft_stalled_threshold=data.get("soft_stalled_threshold", 3),
            stalled_threshold=data.get("stalled_threshold", 3),
            max_retries=data.get("max_retries", 3),
            retry_backoff_seconds=data.get("retry_backoff_seconds", 5),
            notification_settings=data.get("notification_settings", {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Load config from a JSON file at *path*."""
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


class Store(ABC):
    """Abstract persistence interface for tasks.

    Implementations must be thread-safe and support atomic claim operations.
    """

    @abstractmethod
    def add_task(self, task: Task) -> None:
        """Persist a new task. Raises if a task with the same ID exists."""

    @abstractmethod
    def get_task(self, task_id: str, project_path: str = "") -> Optional[Task]:
        """Retrieve a task by its unique identifier."""

    @abstractmethod
    def update_task(self, task: Task) -> None:
        """Update an existing task. Raises if the task does not exist."""

    @abstractmethod
    def delete_task(self, task_id: str, project_path: str = "") -> None:
        """Remove a task by its unique identifier."""

    @abstractmethod
    def get_next_pending(self, project_path: str = "") -> Optional[Task]:
        """Return the next pending task in queue order, or None."""

    @abstractmethod
    def claim_next(self, project_path: str = "") -> Optional[Task]:
        """Atomically claim the next pending task.

        Returns the task with its status updated to RUNNING, or None if no
        pending task is available. Must be safe under concurrent access.
        """

    @abstractmethod
    def list_tasks(
        self, status: Optional[TaskStatus] = None, project_path: str = ""
    ) -> list[Task]:
        """Return all tasks, optionally filtered by status."""

    @abstractmethod
    def get_running_task(self, project_path: str = "") -> Optional[Task]:
        """Return the currently running task, or None."""

    @abstractmethod
    def reorder_task(
        self, task_id: str, new_order: int, project_path: str = ""
    ) -> None: ...

    @abstractmethod
    def update_status(
        self, task_id: str, status: TaskStatus, project_path: str = ""
    ) -> None: ...


class SQLiteStore(Store):
    """SQLite-backed implementation of the Store interface.

    Uses a single connection per thread and serializes access via a mutex
    around the atomic claim operation.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        """Return a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _ensure_schema(self) -> None:
        """Create tables if they do not already exist."""
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    project_path TEXT NOT NULL DEFAULT '',
                    target_session_id TEXT,
                    status TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_message TEXT,
                    "order" INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "project_path" not in columns:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN project_path TEXT NOT NULL DEFAULT ''"
                )
            if "target_session_id" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN target_session_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)")
            conn.execute('CREATE INDEX IF NOT EXISTS idx_order ON tasks("order")')
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_path, status)"
            )

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """Convert a SQLite row into a Task dataclass instance."""
        return Task(
            id=row["id"],
            title=row["title"],
            prompt=row["prompt"],
            mode=ExecutionMode(row["mode"]),
            project_path=row["project_path"],
            target_session_id=row["target_session_id"],
            status=TaskStatus(row["status"]),
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            error_message=row["error_message"],
            order=row["order"],
        )

    def add_task(self, task: Task) -> None:
        """Persist a new task. Raises if a task with the same ID exists."""
        with self._conn() as conn:
            if task.order <= 0:
                task.order = conn.execute(
                    'SELECT COALESCE(MAX("order"), 0) + 1 FROM tasks'
                ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO tasks (
                    id, title, prompt, mode, status, retry_count, max_retries,
                    project_path, target_session_id, created_at, updated_at, completed_at, error_message, "order"
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.title,
                    task.prompt,
                    task.mode.value,
                    task.status.value,
                    task.retry_count,
                    task.max_retries,
                    task.project_path,
                    task.target_session_id,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                    task.error_message,
                    task.order,
                ),
            )

    def get_task(self, task_id: str, project_path: str = "") -> Optional[Task]:
        """Retrieve a task by its unique identifier."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ? AND project_path = ?",
                (task_id, project_path),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def update_task(self, task: Task) -> None:
        """Update an existing task. Raises if the task does not exist."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks SET
                    title = ?,
                    prompt = ?,
                    mode = ?,
                    project_path = ?,
                    target_session_id = ?,
                    status = ?,
                    retry_count = ?,
                    max_retries = ?,
                    created_at = ?,
                    updated_at = ?,
                    completed_at = ?,
                    error_message = ?,
                    "order" = ?
                WHERE id = ?
                """,
                (
                    task.title,
                    task.prompt,
                    task.mode.value,
                    task.project_path,
                    task.target_session_id,
                    task.status.value,
                    task.retry_count,
                    task.max_retries,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                    task.error_message,
                    task.order,
                    task.id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Task not found: {task.id}")

    def delete_task(self, task_id: str, project_path: str = "") -> None:
        """Remove a task by its unique identifier."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM tasks WHERE id = ? AND project_path = ?",
                (task_id, project_path),
            )

    def get_next_pending(self, project_path: str = "") -> Optional[Task]:
        """Return the next pending task in queue order, or None."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = ? AND project_path = ?
                ORDER BY "order" ASC, created_at ASC
                LIMIT 1
                """,
                (TaskStatus.PENDING.value, project_path),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def claim_next(self, project_path: str = "") -> Optional[Task]:
        """Atomically claim the next pending task.

        Uses an explicit transaction with row-level locking to prevent
        duplicate claims under concurrent access.
        """
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE status = ? AND project_path = ?
                    ORDER BY "order" ASC, created_at ASC
                    LIMIT 1
                    """,
                    (TaskStatus.PENDING.value, project_path),
                ).fetchone()

                if row is None:
                    conn.execute("ROLLBACK")
                    return None

                task = self._row_to_task(row)
                now = datetime.utcnow()
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (TaskStatus.RUNNING.value, now.isoformat(), task.id),
                )
                conn.execute("COMMIT")

                task.status = TaskStatus.RUNNING
                task.updated_at = now
                return task
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def list_tasks(
        self, status: Optional[TaskStatus] = None, project_path: str = ""
    ) -> list[Task]:
        """Return all tasks, optionally filtered by status."""
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    'SELECT * FROM tasks WHERE status = ? AND project_path = ? ORDER BY "order" ASC',
                    (status.value, project_path),
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM tasks WHERE project_path = ? ORDER BY "order" ASC',
                    (project_path,),
                ).fetchall()
            return [self._row_to_task(row) for row in rows]

    def list_active_tasks(self, project_path: str = "") -> list[Task]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE project_path = ? AND status IN (?, ?, ?)
                ORDER BY CASE status
                    WHEN ? THEN 0
                    WHEN ? THEN 1
                    ELSE 2
                END,
                "order" ASC,
                created_at ASC
                """,
                (
                    project_path,
                    TaskStatus.RUNNING.value,
                    TaskStatus.RETRY_WAIT.value,
                    TaskStatus.PENDING.value,
                    TaskStatus.RUNNING.value,
                    TaskStatus.RETRY_WAIT.value,
                ),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_completed_tasks(self, project_path: str = "") -> list[Task]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE project_path = ? AND status IN (?, ?)
                ORDER BY completed_at DESC, updated_at DESC, created_at DESC
                """,
                (project_path, TaskStatus.DONE.value, TaskStatus.SKIPPED.value),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_running_task(self, project_path: str = "") -> Optional[Task]:
        """Return the currently running task, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status = ? AND project_path = ? LIMIT 1",
                (TaskStatus.RUNNING.value, project_path),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def reorder_task(
        self, task_id: str, new_order: int, project_path: str = ""
    ) -> None:
        with self._conn() as conn:
            now = datetime.utcnow().isoformat()
            cursor = conn.execute(
                'UPDATE tasks SET "order" = ?, updated_at = ? WHERE id = ? AND project_path = ?',
                (new_order, now, task_id, project_path),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Task not found: {task_id}")

    def update_status(
        self, task_id: str, status: TaskStatus, project_path: str = ""
    ) -> None:
        with self._conn() as conn:
            now = datetime.utcnow()
            completed_at = (
                now.isoformat()
                if status in (TaskStatus.DONE, TaskStatus.SKIPPED)
                else None
            )
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?, updated_at = ?, completed_at = ?
                WHERE id = ? AND project_path = ?
                """,
                (status.value, now.isoformat(), completed_at, task_id, project_path),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Task not found: {task_id}")

    def close(self) -> None:
        """Close the thread-local connection if open."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
