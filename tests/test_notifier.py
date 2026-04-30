from __future__ import annotations

import smtplib
import socket
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from omo_task_queue.notifier import (
    EmailNotifier,
    MockNotifier,
    NotificationConfig,
)
from omo_task_queue.state import ExecutionMode, Task, TaskStatus


def _make_task(status: TaskStatus = TaskStatus.DONE) -> Task:
    return Task(
        id="task-1",
        title="Test Task",
        prompt="Do something",
        mode=ExecutionMode.ONE_SHOT,
        status=status,
        completed_at=datetime.utcnow()
        if status in (TaskStatus.DONE, TaskStatus.SKIPPED)
        else None,
    )


class TestNotificationConfig:
    def test_defaults(self) -> None:
        cfg = NotificationConfig()
        assert cfg.enabled is False
        assert cfg.smtp_host == "localhost"
        assert cfg.smtp_port == 587
        assert cfg.smtp_use_tls is True
        assert cfg.smtp_use_ssl is False
        assert cfg.recipient == ""
        assert cfg.sender == ""

    def test_custom_values(self) -> None:
        cfg = NotificationConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="alice",
            smtp_password="secret",
            smtp_use_tls=False,
            smtp_use_ssl=True,
            recipient="alice@example.com",
            sender="bot@example.com",
        )
        assert cfg.enabled is True
        assert cfg.smtp_host == "smtp.example.com"
        assert cfg.smtp_port == 465
        assert cfg.smtp_user == "alice"
        assert cfg.smtp_password == "secret"
        assert cfg.smtp_use_tls is False
        assert cfg.smtp_use_ssl is True
        assert cfg.recipient == "alice@example.com"
        assert cfg.sender == "bot@example.com"


class TestMockNotifier:
    def test_success_notification_recorded(self) -> None:
        notifier = MockNotifier()
        task = _make_task(TaskStatus.DONE)
        notifier.send_success_notification(task)
        assert len(notifier.sent) == 1
        assert notifier.sent[0].id == "task-1"

    def test_disabled_does_not_record(self) -> None:
        notifier = MockNotifier(config=NotificationConfig(enabled=False))
        task = _make_task(TaskStatus.DONE)
        notifier.send_success_notification(task)
        assert len(notifier.sent) == 0

    def test_non_done_status_not_recorded(self) -> None:
        notifier = MockNotifier()
        for status in (
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.RETRY_WAIT,
            TaskStatus.SKIPPED,
        ):
            notifier.clear()
            task = _make_task(status)
            notifier.send_success_notification(task)
            assert len(notifier.sent) == 0, (
                f"Expected no notification for {status.value}"
            )

    def test_test_smtp_connection(self) -> None:
        notifier = MockNotifier()
        assert notifier.test_smtp_connection() is True
        notifier.set_test_result(False)
        assert notifier.test_smtp_connection() is False
        assert notifier.test_results == [True, False]

    def test_queue_completion_notification_recorded(self) -> None:
        notifier = MockNotifier()
        completed = _make_task(TaskStatus.DONE)
        next_task = Task(
            id="task-2",
            title="Next Task",
            prompt="Do next",
            mode=ExecutionMode.ONE_SHOT,
        )
        notifier.send_queue_completion_notification(completed, next_task)
        assert notifier.queue_completion_sent == [(completed, next_task)]

    def test_clear(self) -> None:
        notifier = MockNotifier()
        notifier.send_success_notification(_make_task(TaskStatus.DONE))
        notifier.test_smtp_connection()
        notifier.clear()
        assert len(notifier.sent) == 0
        assert len(notifier.test_results) == 0


class TestEmailNotifierSuccessOnly:
    def test_disabled_does_not_send(self) -> None:
        notifier = EmailNotifier(NotificationConfig(enabled=False))
        task = _make_task(TaskStatus.DONE)
        with patch.object(notifier, "_send_mail") as mock_send:
            notifier.send_success_notification(task)
            mock_send.assert_not_called()

    def test_done_status_sends(self) -> None:
        notifier = EmailNotifier(NotificationConfig(enabled=True))
        task = _make_task(TaskStatus.DONE)
        with patch.object(notifier, "_send_mail") as mock_send:
            notifier.send_success_notification(task)
            mock_send.assert_called_once()
            subject, body = mock_send.call_args[0]
            assert "Success: Test Task" in subject
            assert "task-1" in body

    def test_non_done_status_does_not_send(self) -> None:
        notifier = EmailNotifier(NotificationConfig(enabled=True))
        for status in (
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.RETRY_WAIT,
            TaskStatus.SKIPPED,
        ):
            task = _make_task(status)
            with patch.object(notifier, "_send_mail") as mock_send:
                notifier.send_success_notification(task)
                mock_send.assert_not_called()

    def test_queue_completion_notification_with_next_task(self) -> None:
        notifier = EmailNotifier(NotificationConfig(enabled=True))
        completed = _make_task(TaskStatus.DONE)
        next_task = Task(
            id="task-2",
            title="Next Task",
            prompt="Do next",
            mode=ExecutionMode.ONE_SHOT,
        )
        with patch.object(notifier, "_send_mail") as mock_send:
            notifier.send_queue_completion_notification(completed, next_task)
            mock_send.assert_called_once()
            subject, body = mock_send.call_args[0]
            assert "完成: Test Task" in subject
            assert "Test Task 任务已经完成，开始下一个任务 Next Task。" == body

    def test_queue_completion_notification_when_all_done(self) -> None:
        notifier = EmailNotifier(NotificationConfig(enabled=True))
        completed = _make_task(TaskStatus.DONE)
        with patch.object(notifier, "_send_mail") as mock_send:
            notifier.send_queue_completion_notification(completed, None)
            mock_send.assert_called_once()
            subject, body = mock_send.call_args[0]
            assert "完成: Test Task" in subject
            assert "Test Task 任务已经完成，全部任务结束。" == body


class TestEmailNotifierIsolation:
    def test_smtp_exception_swallowed(self) -> None:
        notifier = EmailNotifier(NotificationConfig(enabled=True))
        task = _make_task(TaskStatus.DONE)
        with patch.object(
            notifier, "_send_mail", side_effect=smtplib.SMTPException("boom")
        ):
            notifier.send_success_notification(task)

    def test_generic_exception_swallowed(self) -> None:
        notifier = EmailNotifier(NotificationConfig(enabled=True))
        task = _make_task(TaskStatus.DONE)
        with patch.object(notifier, "_send_mail", side_effect=RuntimeError("boom")):
            notifier.send_success_notification(task)

    def test_task_unchanged_on_failure(self) -> None:
        notifier = EmailNotifier(NotificationConfig(enabled=True))
        task = _make_task(TaskStatus.DONE)
        original_status = task.status
        with patch.object(
            notifier, "_send_mail", side_effect=smtplib.SMTPException("boom")
        ):
            notifier.send_success_notification(task)
        assert task.status is original_status


class TestEmailNotifierTestConnection:
    def test_success(self) -> None:
        notifier = EmailNotifier(
            NotificationConfig(
                enabled=True, smtp_host="smtp.example.com", smtp_port=587
            )
        )
        mock_server = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_server)
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("smtplib.SMTP", return_value=mock_cm):
            result = notifier.test_smtp_connection()
        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.noop.assert_called_once()

    def test_ssl_connection(self) -> None:
        notifier = EmailNotifier(
            NotificationConfig(
                enabled=True,
                smtp_host="smtp.126.com",
                smtp_port=587,
                smtp_use_tls=False,
                smtp_use_ssl=True,
            )
        )
        mock_server = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_server)
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("smtplib.SMTP_SSL", return_value=mock_cm) as mock_ssl:
            result = notifier.test_smtp_connection()
        assert result is True
        mock_ssl.assert_called_once()
        mock_server.starttls.assert_not_called()
        mock_server.noop.assert_called_once()

    def test_failure(self) -> None:
        notifier = EmailNotifier(
            NotificationConfig(enabled=True, smtp_host="bad.host", smtp_port=587)
        )
        with patch("smtplib.SMTP", side_effect=OSError("connection refused")):
            assert notifier.test_smtp_connection() is False

    def test_dns_fallback_resolution_used_when_system_dns_fails(self) -> None:
        notifier = EmailNotifier(
            NotificationConfig(enabled=True, smtp_host="smtp.126.com", smtp_port=587)
        )
        mock_server = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_server)
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch.object(
            notifier,
            "_open_server_with_host",
            side_effect=socket.gaierror(8, "dns failed"),
        ):
            with patch.object(
                notifier, "_resolve_via_http_dns", return_value="220.181.12.16"
            ) as mock_resolve:
                with patch("smtplib.SMTP", return_value=mock_cm) as mock_smtp:
                    result = notifier.test_smtp_connection()
        assert result is True
        mock_resolve.assert_called_once_with("smtp.126.com", 10)
        mock_smtp.assert_called_once_with(timeout=10)
        mock_cm.connect.assert_called_once_with("220.181.12.16", 587)
        assert mock_cm._host == "smtp.126.com"
        mock_server.starttls.assert_called_once()

    def test_dns_failure_without_fallback_returns_false(self) -> None:
        notifier = EmailNotifier(
            NotificationConfig(enabled=True, smtp_host="smtp.126.com", smtp_port=587)
        )
        with patch.object(
            notifier,
            "_open_server_with_host",
            side_effect=socket.gaierror(8, "dns failed"),
        ):
            with patch.object(notifier, "_resolve_via_http_dns", return_value=None):
                assert notifier.test_smtp_connection() is False

    def test_auth_when_credentials_present(self) -> None:
        notifier = EmailNotifier(
            NotificationConfig(
                enabled=True,
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_user="alice",
                smtp_password="secret",
            )
        )
        mock_server = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_server)
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("smtplib.SMTP", return_value=mock_cm):
            notifier.test_smtp_connection()
        mock_server.login.assert_called_once_with("alice", "secret")

    def test_no_tls_when_disabled(self) -> None:
        notifier = EmailNotifier(
            NotificationConfig(
                enabled=True,
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_use_tls=False,
            )
        )
        mock_server = MagicMock()
        with patch("smtplib.SMTP", return_value=mock_server):
            notifier.test_smtp_connection()
            mock_server.starttls.assert_not_called()

    def test_send_test_requires_recipient(self) -> None:
        notifier = EmailNotifier(NotificationConfig(enabled=True, recipient=""))
        with pytest.raises(ValueError, match="未配置测试邮件收件人"):
            notifier.send_test()
