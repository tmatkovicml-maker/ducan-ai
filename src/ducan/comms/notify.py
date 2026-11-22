"""Notifications to staff and the owner.

Email (SMTP) for now; Telegram/WhatsApp can be added later as another
:class:`Notifier`. Every message ends with the AI disclosure line.
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import Protocol

from ducan.comms.disclosure import DISCLOSURE_HR
from ducan.config import EnvSettings


class Notifier(Protocol):
    """Delivers a short text message to a person."""

    def send(self, to: str, subject: str, body: str) -> bool:
        """Return True if the message was handed to the provider."""


class EmailNotifier:
    """Sends plain-text email through the configured SMTP server."""

    def __init__(self, env: EnvSettings) -> None:
        from ducan.actions.executors.purchase_order import SmtpMailer

        self.env = env
        self.mailer = SmtpMailer(env)

    def send(self, to: str, subject: str, body: str) -> bool:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.env.smtp_from
        message["To"] = to
        message.set_content(f"{body}\n\n--\n{DISCLOSURE_HR}")
        self.mailer.send(message)
        return True


class NullNotifier:
    """Used when no channel is configured: nothing is sent."""

    def send(self, to: str, subject: str, body: str) -> bool:
        return False


def make_notifier(env: EnvSettings) -> Notifier:
    """Email if SMTP is configured, otherwise a notifier that sends nothing."""
    if env.smtp_host and env.smtp_from:
        return EmailNotifier(env)
    return NullNotifier()
