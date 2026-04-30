"""Tests for the persistence layer and configuration."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from omo_task_queue.state import ExecutionMode, Task, TaskStatus
from omo_task_queue.store import Config, SQLiteStore


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Provide a temporary database file path."""
    return tmp_path / "test.db"


@pytest.fixture
def store(tmp_db: Path) -> SQLiteStore:
    """Provide a fresh SQLiteStore instance."""
    return SQLiteStore(tmp_db)


@pytest.fixture
def sample_task() -> Task:
    """Provide a sample pending task."""
    return Task(
        id="task-1",
        title="Test Task",
        prompt="Do something",
        mode=ExecutionMode.ONE_SHOT,
        status=TaskStatus.PENDING,
        order=1,
    )


class TestStoreRoundTrip:
    """Basic CRUD and retrieval operations."""

    def test_add_and_get_task(self, store: SQLiteStore, sample_task: Task) -> None:
        store.add_task(sample_task)
        retrieved = store.get_task(sample_task.id)
        assert retrieved is not None
        assert retrieved.id == sample_task.id
        assert retrieved.title == sample_task.title
        assert retrieved.status == TaskStatus.PENDING

    def test_add_duplicate_raises(self, store: SQLiteStore, sample_task: Task) -> None:
        store.add_task(sample_task)
        with pytest.raises(Exception):
            store.add_task(sample_task)

    def test_update_task(self, store: SQLiteStore, sample_task: Task) -> None:
        store.add_task(sample_task)
        sample_task.status = TaskStatus.RUNNING
        sample_task.updated_at = datetime.utcnow()
        store.update_task(sample_task)
        retrieved = store.get_task(sample_task.id)
        assert retrieved is not None
        assert retrieved.status == TaskStatus.RUNNING

    def test_update_missing_task_raises(self, store: SQLiteStore) -> None:
        task = Task(
            id="missing",
            title="Missing",
            prompt="N/A",
            mode=ExecutionMode.ONE_SHOT,
        )
        with pytest.raises(KeyError):
            store.update_task(task)

    def test_delete_task(self, store: SQLiteStore, sample_task: Task) -> None:
        store.add_task(sample_task)
        store.delete_task(sample_task.id)
        assert store.get_task(sample_task.id) is None

    def test_list_tasks(self, store: SQLiteStore) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=2,
        )
        t2 = Task(
            id="b",
            title="B",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RUNNING,
            order=1,
        )
        t3 = Task(
            id="c",
            title="C",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.DONE,
            order=3,
        )
        store.add_task(t1)
        store.add_task(t2)
        store.add_task(t3)

        all_tasks = store.list_tasks()
        assert len(all_tasks) == 3
        assert [t.id for t in all_tasks] == ["b", "a", "c"]

        pending = store.list_tasks(TaskStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].id == "a"

    def test_get_next_pending(self, store: SQLiteStore) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=2,
        )
        t2 = Task(
            id="b",
            title="B",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=1,
        )
        store.add_task(t1)
        store.add_task(t2)

        next_task = store.get_next_pending()
        assert next_task is not None
        assert next_task.id == "b"

    def test_add_task_preserves_fifo_queue_order(self, store: SQLiteStore) -> None:
        first = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
        )
        second = Task(
            id="b",
            title="B",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
        )
        store.add_task(first)
        store.add_task(second)

        assert store.get_next_pending().id == "a"

    def test_list_active_and_completed_tasks(self, store: SQLiteStore) -> None:
        pending = Task(
            id="pending",
            title="Pending",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=1,
        )
        running = Task(
            id="running",
            title="Running",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RUNNING,
            order=2,
        )
        retry_wait = Task(
            id="retry",
            title="Retry",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RETRY_WAIT,
            order=3,
        )
        done = Task(
            id="done",
            title="Done",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.DONE,
            order=4,
        )
        skipped = Task(
            id="skipped",
            title="Skipped",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.SKIPPED,
            order=5,
        )
        done.completed_at = datetime(2024, 1, 1, 0, 0, 0)
        skipped.completed_at = datetime(2024, 1, 1, 0, 1, 0)

        for task in [pending, running, retry_wait, done, skipped]:
            store.add_task(task)

        assert [task.id for task in store.list_active_tasks()] == [
            "running",
            "retry",
            "pending",
        ]
        assert [task.id for task in store.list_completed_tasks()] == [
            "skipped",
            "done",
        ]

    def test_project_scoped_claim_and_listing(self, store: SQLiteStore) -> None:
        project_a = "/tmp/project-a"
        project_b = "/tmp/project-b"
        store.add_task(
            Task(
                id="a1",
                title="A1",
                prompt="p",
                mode=ExecutionMode.ONE_SHOT,
                status=TaskStatus.PENDING,
                order=1,
                project_path=project_a,
            )
        )
        store.add_task(
            Task(
                id="b1",
                title="B1",
                prompt="p",
                mode=ExecutionMode.ONE_SHOT,
                status=TaskStatus.PENDING,
                order=1,
                project_path=project_b,
            )
        )

        assert [task.id for task in store.list_tasks(project_path=project_a)] == ["a1"]
        assert [task.id for task in store.list_tasks(project_path=project_b)] == ["b1"]

        claimed_a = store.claim_next(project_path=project_a)
        assert claimed_a is not None
        assert claimed_a.id == "a1"
        assert store.get_running_task(project_path=project_a).id == "a1"
        assert store.get_running_task(project_path=project_b) is None
        assert store.get_next_pending(project_path=project_b).id == "b1"

    def test_get_next_pending_empty(self, store: SQLiteStore) -> None:
        assert store.get_next_pending() is None

    def test_get_running_task(self, store: SQLiteStore) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RUNNING,
            order=1,
        )
        store.add_task(t1)
        running = store.get_running_task()
        assert running is not None
        assert running.id == "a"

    def test_get_running_task_none(self, store: SQLiteStore) -> None:
        assert store.get_running_task() is None


class TestAtomicClaim:
    """Atomic claim_next under concurrent access."""

    def test_claim_next_returns_task(
        self, store: SQLiteStore, sample_task: Task
    ) -> None:
        store.add_task(sample_task)
        claimed = store.claim_next()
        assert claimed is not None
        assert claimed.id == sample_task.id
        assert claimed.status == TaskStatus.RUNNING

    def test_claim_next_updates_db(self, store: SQLiteStore, sample_task: Task) -> None:
        store.add_task(sample_task)
        store.claim_next()
        retrieved = store.get_task(sample_task.id)
        assert retrieved is not None
        assert retrieved.status == TaskStatus.RUNNING

    def test_claim_next_empty_queue(self, store: SQLiteStore) -> None:
        assert store.claim_next() is None

    def test_claim_next_no_duplicate_claims(self, store: SQLiteStore) -> None:
        """Under concurrent threads, exactly one claim succeeds."""
        for i in range(5):
            store.add_task(
                Task(
                    id=f"task-{i}",
                    title=f"Task {i}",
                    prompt="p",
                    mode=ExecutionMode.ONE_SHOT,
                    status=TaskStatus.PENDING,
                    order=i,
                )
            )

        results: list[Task | None] = []
        errors: list[Exception] = []

        def claim() -> None:
            try:
                task = store.claim_next()
                if task:
                    results.append(task)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=claim) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 5
        claimed_ids = {t.id for t in results}
        assert len(claimed_ids) == 5

    def test_claim_next_prevents_race_condition(self, store: SQLiteStore) -> None:
        """Stress test: many threads racing for a single task."""
        store.add_task(
            Task(
                id="solo",
                title="Solo",
                prompt="p",
                mode=ExecutionMode.ONE_SHOT,
                status=TaskStatus.PENDING,
                order=0,
            )
        )

        successes = []
        lock = threading.Lock()

        def claim() -> None:
            task = store.claim_next()
            if task:
                with lock:
                    successes.append(task.id)

        threads = [threading.Thread(target=claim) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 1


class TestConfigPersistence:
    """Configuration save/load round-trip."""

    def test_config_defaults(self) -> None:
        cfg = Config()
        assert cfg.idle_threshold == 3
        assert cfg.soft_stalled_threshold == 3
        assert cfg.stalled_threshold == 3
        assert cfg.max_retries == 3
        assert cfg.retry_backoff_seconds == 5
        assert cfg.notification_settings == {}

    def test_config_to_dict(self) -> None:
        cfg = Config(idle_threshold=60, max_retries=5, retry_backoff_seconds=10)
        d = cfg.to_dict()
        assert d["idle_threshold"] == 60
        assert d["max_retries"] == 5
        assert d["retry_backoff_seconds"] == 10

    def test_config_from_dict(self) -> None:
        data = {
            "idle_threshold": 45,
            "max_retries": 2,
            "retry_backoff_seconds": 3,
            "notification_settings": {"enabled": True},
        }
        cfg = Config.from_dict(data)
        assert cfg.idle_threshold == 45
        assert cfg.max_retries == 2
        assert cfg.retry_backoff_seconds == 3
        assert cfg.notification_settings == {"enabled": True}

    def test_config_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        cfg = Config(
            idle_threshold=120,
            max_retries=10,
            retry_backoff_seconds=15,
            notification_settings={"enabled": True, "smtp_host": "smtp.example.com"},
        )
        cfg.save(path)
        loaded = Config.load(path)
        assert loaded.idle_threshold == 120
        assert loaded.max_retries == 10
        assert loaded.retry_backoff_seconds == 15
        assert loaded.notification_settings == {
            "enabled": True,
            "smtp_host": "smtp.example.com",
        }

    def test_config_load_missing_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.json"
        cfg = Config.load(path)
        assert cfg.idle_threshold == 3
        assert cfg.max_retries == 3

    def test_config_from_dict_defaults(self) -> None:
        cfg = Config.from_dict({})
        assert cfg.idle_threshold == 3
        assert cfg.max_retries == 3
        assert cfg.retry_backoff_seconds == 5


class TestReorderAndStatus:
    def test_reorder_task(self, store: SQLiteStore) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=1,
        )
        store.add_task(t1)
        store.reorder_task("a", 99)
        retrieved = store.get_task("a")
        assert retrieved is not None
        assert retrieved.order == 99

    def test_reorder_task_updates_timestamp(self, store: SQLiteStore) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=1,
        )
        store.add_task(t1)
        before = t1.updated_at
        store.reorder_task("a", 5)
        retrieved = store.get_task("a")
        assert retrieved is not None
        assert retrieved.updated_at > before

    def test_reorder_missing_task_raises(self, store: SQLiteStore) -> None:
        with pytest.raises(KeyError):
            store.reorder_task("missing", 1)

    def test_update_status(self, store: SQLiteStore) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=1,
        )
        store.add_task(t1)
        store.update_status("a", TaskStatus.RUNNING)

    def test_update_status_sets_completed_at_for_done(self, store: SQLiteStore) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=1,
        )
        store.add_task(t1)
        store.update_status("a", TaskStatus.DONE)
        retrieved = store.get_task("a")
        assert retrieved is not None
        assert retrieved.completed_at is not None

    def test_update_status_sets_completed_at_for_skipped(
        self, store: SQLiteStore
    ) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=1,
        )
        store.add_task(t1)
        store.update_status("a", TaskStatus.SKIPPED)
        retrieved = store.get_task("a")
        assert retrieved is not None
        assert retrieved.completed_at is not None

    def test_update_status_does_not_set_completed_at_for_pending(
        self, store: SQLiteStore
    ) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RUNNING,
            order=1,
        )
        store.add_task(t1)
        store.update_status("a", TaskStatus.PENDING)
        retrieved = store.get_task("a")
        assert retrieved is not None
        assert retrieved.completed_at is None

    def test_update_status_updates_timestamp(self, store: SQLiteStore) -> None:
        t1 = Task(
            id="a",
            title="A",
            prompt="p",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.PENDING,
            order=1,
        )
        store.add_task(t1)
        before = t1.updated_at
        store.update_status("a", TaskStatus.RUNNING)
        retrieved = store.get_task("a")
        assert retrieved is not None
        assert retrieved.updated_at > before

    def test_update_status_missing_task_raises(self, store: SQLiteStore) -> None:
        with pytest.raises(KeyError):
            store.update_status("missing", TaskStatus.DONE)
