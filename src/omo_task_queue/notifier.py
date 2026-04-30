from __future__ import annotations

import json
import logging
import smtplib
import socket
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Optional
from urllib.parse import quote
from urllib.request import urlopen
from typing_extensions import Protocol

from omo_task_queue.state import ExecutionMode, Task, TaskStatus

logger = logging.getLogger(__name__)

_DNS_HTTP_RESOLVERS = (
    "http://223.5.5.5/resolve?name={name}&type=A&short=1",
    "http://223.6.6.6/resolve?name={name}&type=A&short=1",
)


@dataclass
class NotificationConfig:
    enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    recipient: str = ""
    sender: str = ""


class Notifier(Protocol):
    def send_success_notification(self, task: Task) -> None: ...

    def send_queue_completion_notification(
        self, completed_task: Task, next_task: Optional[Task]
    ) -> None: ...

    def test_smtp_connection(self) -> bool: ...

    def send_test(self, recipient: Optional[str] = None) -> None: ...


class EmailNotifier:
    def __init__(self, config: NotificationConfig) -> None:
        self.config = config

    def send_success_notification(self, task: Task) -> None:
        if not self.config.enabled:
            return

        if task.status is not TaskStatus.DONE:
            logger.warning(
                "Notifier skipped: task %s status is %s, expected DONE",
                task.id,
                task.status.value,
            )
            return

        subject = f"[OMO Task Queue] Success: {task.title}"
        body = self._build_body(task)

        try:
            self._send_mail(subject, body)
            logger.info("Success notification sent for task %s", task.id)
        except smtplib.SMTPException as exc:
            logger.error(
                "SMTP error sending notification for task %s: %s", task.id, exc
            )
        except Exception as exc:
            logger.error(
                "Unexpected error sending notification for task %s: %s", task.id, exc
            )

    def send_queue_completion_notification(
        self, completed_task: Task, next_task: Optional[Task]
    ) -> None:
        if not self.config.enabled:
            return

        if completed_task.status is not TaskStatus.DONE:
            logger.warning(
                "Queue completion notifier skipped: task %s status is %s, expected DONE",
                completed_task.id,
                completed_task.status.value,
            )
            return

        subject = f"[OMO Task Queue] 完成: {completed_task.title}"
        if next_task is not None:
            body = f"{completed_task.title} 任务已经完成，开始下一个任务 {next_task.title}。"
        else:
            body = f"{completed_task.title} 任务已经完成，全部任务结束。"

        try:
            self._send_mail(subject, body)
            logger.info(
                "Queue completion notification sent for task %s", completed_task.id
            )
        except smtplib.SMTPException as exc:
            logger.error(
                "SMTP error sending queue completion notification for task %s: %s",
                completed_task.id,
                exc,
            )
        except Exception as exc:
            logger.error(
                "Unexpected error sending queue completion notification for task %s: %s",
                completed_task.id,
                exc,
            )

    def test_smtp_connection(self) -> bool:
        try:
            with self._open_server(timeout=10) as server:
                self._configure_server(server)
                server.noop()
            return True
        except Exception as exc:
            logger.error("SMTP test connection failed: %s", exc)
            return False

    def send_test(self, recipient: Optional[str] = None) -> None:
        target = recipient or self.config.recipient
        if not target:
            raise ValueError("未配置测试邮件收件人")

        subject = "[OMO 任务队列] 测试邮件"
        body = (
            "这是一封来自 OMO 任务队列的测试邮件。\n\n"
            "如果你收到了这封邮件，说明当前通知配置可用。"
        )
        try:
            self._send_mail(subject, body, to=target)
            logger.info("Test notification sent to %s", target)
        except smtplib.SMTPException as exc:
            logger.error("SMTP error sending test notification: %s", exc)
            raise
        except Exception as exc:
            logger.error("Unexpected error sending test notification: %s", exc)
            raise

    def _build_body(self, task: Task) -> str:
        lines = [
            f"任务标题: {task.title}",
            f"ID: {task.id}",
            f"执行模式: {task.mode.value}",
            f"完成时间: {task.completed_at}",
            "",
            "该任务已通过 OMO 任务队列自动完成。",
        ]
        return "\n".join(lines)

    def _open_server(self, timeout: int):
        try:
            return self._open_server_with_host(self.config.smtp_host, timeout)
        except socket.gaierror:
            resolved = self._resolve_via_http_dns(self.config.smtp_host, timeout)
            if resolved is None:
                raise
            logger.warning(
                "System DNS failed for %s, retrying with resolved IP %s",
                self.config.smtp_host,
                resolved,
            )
            return self._open_server_with_resolved_ip(
                hostname=self.config.smtp_host,
                resolved_ip=resolved,
                timeout=timeout,
            )

    def _open_server_with_host(self, host: str, timeout: int):
        if self.config.smtp_use_ssl:
            return smtplib.SMTP_SSL(host, self.config.smtp_port, timeout=timeout)
        return smtplib.SMTP(host, self.config.smtp_port, timeout=timeout)

    def _open_server_with_resolved_ip(
        self, hostname: str, resolved_ip: str, timeout: int
    ):
        if self.config.smtp_use_ssl:
            server = smtplib.SMTP_SSL(timeout=timeout)
        else:
            server = smtplib.SMTP(timeout=timeout)
        server._host = hostname
        server.connect(resolved_ip, self.config.smtp_port)
        return server

    def _resolve_via_http_dns(self, hostname: str, timeout: int) -> Optional[str]:
        encoded_name = quote(hostname, safe="")
        request_timeout = max(1, min(timeout, 5))
        for resolver in _DNS_HTTP_RESOLVERS:
            url = resolver.format(name=encoded_name)
            try:
                with urlopen(url, timeout=request_timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception:
                continue
            resolved = self._extract_first_ip(payload)
            if resolved is not None:
                return resolved
        return None

    def _extract_first_ip(self, payload: object) -> Optional[str]:
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, str) and self._is_ipv4_address(item):
                    return item
            return None
        if isinstance(payload, dict):
            answers = payload.get("Answer")
            if not isinstance(answers, list):
                return None
            for item in answers:
                if not isinstance(item, dict):
                    continue
                data = item.get("data")
                if isinstance(data, str) and self._is_ipv4_address(data):
                    return data
        return None

    def _is_ipv4_address(self, value: str) -> bool:
        try:
            socket.inet_aton(value)
        except OSError:
            return False
        return value.count(".") == 3

    def _configure_server(self, server) -> None:
        if not self.config.smtp_use_ssl and self.config.smtp_use_tls:
            server.starttls()
        if self.config.smtp_user and self.config.smtp_password:
            server.login(self.config.smtp_user, self.config.smtp_password)

    def _send_mail(self, subject: str, body: str, to: Optional[str] = None) -> None:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.config.sender
        msg["To"] = to or self.config.recipient

        with self._open_server(timeout=30) as server:
            self._configure_server(server)
            server.sendmail(
                self.config.sender, [to or self.config.recipient], msg.as_string()
            )


class MockNotifier:
    def __init__(self, config: Optional[NotificationConfig] = None) -> None:
        self.config = config or NotificationConfig(enabled=True)
        self.sent: list[Task] = []
        self.queue_completion_sent: list[tuple[Task, Optional[Task]]] = []
        self.test_results: list[bool] = []
        self._test_should_succeed: bool = True
        self.last_test_recipient: Optional[str] = None

    def send_success_notification(self, task: Task) -> None:
        if not self.config.enabled:
            return
        if task.status is not TaskStatus.DONE:
            return
        self.sent.append(task)

    def send_queue_completion_notification(
        self, completed_task: Task, next_task: Optional[Task]
    ) -> None:
        if not self.config.enabled:
            return
        if completed_task.status is not TaskStatus.DONE:
            return
        self.queue_completion_sent.append((completed_task, next_task))

    def test_smtp_connection(self) -> bool:
        result = self._test_should_succeed
        self.test_results.append(result)
        return result

    def send_test(self, recipient: Optional[str] = None) -> None:
        self.last_test_recipient = recipient

    def set_test_result(self, succeed: bool) -> None:
        self._test_should_succeed = succeed

    def clear(self) -> None:
        self.sent.clear()
        self.queue_completion_sent.clear()
        self.test_results.clear()
        self.last_test_recipient = None
