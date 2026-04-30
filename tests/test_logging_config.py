from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import pytest

from omo_task_queue.logging_config import (
    AUDIT_LOGGER_NAME,
    AuditLogger,
    TransitionRecord,
    setup_logging,
)
from omo_task_queue.state import ExecutionMode, Task, TaskStatus


class TestSetupLogging:
    def test_creates_root_logger(self) -> None:
        setup_logging(level=logging.DEBUG)
        logger = logging.getLogger("omo_task_queue")
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == 1

    def test_creates_audit_logger(self) -> None:
        setup_logging(level=logging.INFO)
        audit = logging.getLogger(AUDIT_LOGGER_NAME)
        assert audit.level == logging.INFO
        assert len(audit.handlers) == 1
        assert audit.propagate is False

    def test_custom_level(self) -> None:
        setup_logging(level=logging.WARNING)
        logger = logging.getLogger("omo_task_queue")
        assert logger.level == logging.WARNING

    def test_file_handler_created_when_path_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            audit = logging.getLogger(AUDIT_LOGGER_NAME)
            assert any(isinstance(h, logging.FileHandler) for h in audit.handlers)

    def test_directory_path_creates_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            setup_logging(log_path=tmp, level=logging.INFO)
            expected = Path(tmp) / "audit.log"
            assert expected.exists()

    def test_no_duplicate_handlers_on_multiple_calls(self) -> None:
        setup_logging(level=logging.INFO)
        setup_logging(level=logging.INFO)
        logger = logging.getLogger("omo_task_queue")
        audit = logging.getLogger(AUDIT_LOGGER_NAME)
        assert len(logger.handlers) == 1
        assert len(audit.handlers) == 1

    def test_file_handler_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            audit = logging.getLogger(AUDIT_LOGGER_NAME)
            audit.info('{"test": true}')

            content = log_file.read_text()
            assert '{"test": true}' in content


class TestTransitionRecord:
    def test_to_dict(self) -> None:
        record = TransitionRecord(
            timestamp="2024-01-01T00:00:00",
            task_id="t1",
            from_status="pending",
            to_status="running",
            reason="dispatch",
            mode="one_shot",
        )
        d = record.to_dict()
        assert d["task_id"] == "t1"
        assert d["from_status"] == "pending"
        assert d["to_status"] == "running"
        assert d["reason"] == "dispatch"
        assert d["mode"] == "one_shot"
        assert d["timestamp"] == "2024-01-01T00:00:00"

    def test_to_json(self) -> None:
        record = TransitionRecord(
            timestamp="2024-01-01T00:00:00",
            task_id="t1",
            from_status="pending",
            to_status="running",
            reason="dispatch",
            mode="one_shot",
        )
        parsed = json.loads(record.to_json())
        assert parsed["task_id"] == "t1"
        assert parsed["from_status"] == "pending"
        assert parsed["to_status"] == "running"
        assert parsed["reason"] == "dispatch"
        assert parsed["mode"] == "one_shot"

    def test_frozen(self) -> None:
        record = TransitionRecord(
            timestamp="2024-01-01T00:00:00",
            task_id="t1",
            from_status="pending",
            to_status="running",
            reason="dispatch",
            mode="one_shot",
        )
        with pytest.raises(AttributeError):
            record.task_id = "t2"


class TestAuditLoggerLogTransition:
    def test_returns_transition_record(self, caplog) -> None:
        setup_logging(level=logging.INFO)
        caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
        logger = AuditLogger()
        task = Task(
            id="task-1",
            title="Test",
            prompt="Do it",
            mode=ExecutionMode.ONE_SHOT,
            status=TaskStatus.RUNNING,
        )
        result = logger.log_transition(
            task,
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            reason="dispatch",
        )
        assert isinstance(result, TransitionRecord)
        assert result.task_id == "task-1"
        assert result.from_status == "pending"
        assert result.to_status == "running"
        assert result.reason == "dispatch"
        assert result.mode == "one_shot"

    def test_logs_json_to_audit(self, caplog) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            logger = AuditLogger()
            task = Task(
                id="task-2",
                title="Test",
                prompt="Do it",
                mode=ExecutionMode.ULW_LOOP,
                status=TaskStatus.RUNNING,
            )
            logger.log_transition(
                task,
                TaskStatus.RUNNING,
                TaskStatus.DONE,
                reason="completion",
            )
            content = log_file.read_text()
            parsed = json.loads(content.strip())
            assert parsed["task_id"] == "task-2"
            assert parsed["from_status"] == "running"
            assert parsed["to_status"] == "done"
            assert parsed["reason"] == "completion"
            assert parsed["mode"] == "ulw_loop"
            assert "timestamp" in parsed

    def test_default_reason(self, caplog) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            logger = AuditLogger()
            task = Task(
                id="task-3",
                title="Test",
                prompt="Do it",
                mode=ExecutionMode.RALPH_LOOP,
                status=TaskStatus.RUNNING,
            )
            logger.log_transition(task, TaskStatus.RUNNING, TaskStatus.RETRY_WAIT)
            content = log_file.read_text()
            parsed = json.loads(content.strip())
            assert parsed["reason"] == "state_change"


class TestAuditLoggerConvenienceMethods:
    def test_log_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            logger = AuditLogger()
            task = Task(
                id="t1",
                title="Test",
                prompt="Do it",
                mode=ExecutionMode.ONE_SHOT,
            )
            logger.log_dispatch(task)
            content = log_file.read_text()
            parsed = json.loads(content.strip())
            assert parsed["from_status"] == "pending"
            assert parsed["to_status"] == "running"
            assert parsed["reason"] == "dispatch"

    def test_log_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            logger = AuditLogger()
            task = Task(
                id="t1",
                title="Test",
                prompt="Do it",
                mode=ExecutionMode.ONE_SHOT,
                status=TaskStatus.RUNNING,
            )
            logger.log_completion(task)
            content = log_file.read_text()
            parsed = json.loads(content.strip())
            assert parsed["from_status"] == "running"
            assert parsed["to_status"] == "done"
            assert parsed["reason"] == "completion"

    def test_log_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            logger = AuditLogger()
            task = Task(
                id="t1",
                title="Test",
                prompt="Do it",
                mode=ExecutionMode.ONE_SHOT,
                status=TaskStatus.RUNNING,
            )
            logger.log_retry(task)
            content = log_file.read_text()
            parsed = json.loads(content.strip())
            assert parsed["from_status"] == "running"
            assert parsed["to_status"] == "retry_wait"
            assert parsed["reason"] == "retry"

    def test_log_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            logger = AuditLogger()
            task = Task(
                id="t1",
                title="Test",
                prompt="Do it",
                mode=ExecutionMode.ONE_SHOT,
                status=TaskStatus.RETRY_WAIT,
            )
            logger.log_skip(task)
            content = log_file.read_text()
            parsed = json.loads(content.strip())
            assert parsed["from_status"] == "retry_wait"
            assert parsed["to_status"] == "skipped"
            assert parsed["reason"] == "skip"

    def test_log_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            logger = AuditLogger()
            task = Task(
                id="t1",
                title="Test",
                prompt="Do it",
                mode=ExecutionMode.ONE_SHOT,
                status=TaskStatus.RUNNING,
            )
            logger.log_failure(task)
            content = log_file.read_text()
            parsed = json.loads(content.strip())
            assert parsed["from_status"] == "running"
            assert parsed["to_status"] == "retry_wait"
            assert parsed["reason"] == "failure"


class TestAuditLogFileOutput:
    def test_audit_log_written_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            logger = AuditLogger()
            task = Task(
                id="file-task",
                title="Test",
                prompt="Do it",
                mode=ExecutionMode.ONE_SHOT,
                status=TaskStatus.RUNNING,
            )
            logger.log_completion(task)

            content = log_file.read_text()
            parsed = json.loads(content.strip())
            assert parsed["task_id"] == "file-task"
            assert parsed["to_status"] == "done"
            assert parsed["reason"] == "completion"

    def test_multiple_transitions_in_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "audit.log"
            setup_logging(log_path=log_file, level=logging.INFO)
            logger = AuditLogger()
            task = Task(
                id="multi-task",
                title="Test",
                prompt="Do it",
                mode=ExecutionMode.ONE_SHOT,
                status=TaskStatus.PENDING,
            )
            logger.log_dispatch(task)
            task.status = TaskStatus.RUNNING
            logger.log_completion(task)

            lines = log_file.read_text().strip().split("\n")
            assert len(lines) == 2
            first = json.loads(lines[0])
            second = json.loads(lines[1])
            assert first["reason"] == "dispatch"
            assert second["reason"] == "completion"
