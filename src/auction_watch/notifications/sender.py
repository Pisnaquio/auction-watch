"""Notification transport contracts with a deliberately small SMTP adapter."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol


@dataclass(frozen=True)
class NotificationMessage:
    recipient: str
    subject: str
    body: str


class NotificationSender(Protocol):
    """Deliver one already-planned notification."""

    def send(self, message: NotificationMessage) -> None: ...


class SMTPNotificationSender:
    """Send plain-text messages using SMTP configuration supplied at runtime."""

    def __init__(
        self,
        *,
        host: str | None,
        port: int,
        sender: str,
        recipient: str | None,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.sender = sender
        self.recipient = recipient
        self.username = username
        self.password = password
        self.use_tls = use_tls

    def send(self, message: NotificationMessage) -> None:
        if not self.host or not self.recipient:
            raise RuntimeError("SMTP notification is not configured")
        mail = EmailMessage()
        mail["From"] = self.sender
        mail["To"] = self.recipient
        mail["Subject"] = message.subject
        mail.set_content(message.body)
        with smtplib.SMTP(self.host, self.port, timeout=10) as connection:
            if self.use_tls:
                connection.starttls()
            if self.username:
                connection.login(self.username, self.password or "")
            connection.send_message(mail)


class FakeNotificationSender:
    """In-memory sender for tests; it never opens a socket."""

    def __init__(self) -> None:
        self.messages: list[NotificationMessage] = []
        self.failures_remaining = 0

    def send(self, message: NotificationMessage) -> None:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("fake sender failure")
        self.messages.append(message)


__all__ = [
    "FakeNotificationSender",
    "NotificationMessage",
    "NotificationSender",
    "SMTPNotificationSender",
]
