"""Executor for ``purchase_order``: emails the approved order to the supplier."""

from __future__ import annotations

import smtplib
from datetime import date
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ducan.brand import Brand
from ducan.config import AppConfig, EnvSettings
from ducan.models import Action, Supplier
from ducan.replenishment.documents import load_lines, order_csv, order_pdf


class Mailer(Protocol):
    """Sends an email message."""

    def send(self, message: EmailMessage) -> None: ...


class SmtpMailer:
    """Plain SMTP with optional STARTTLS."""

    def __init__(self, env: EnvSettings) -> None:
        self.env = env

    def send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.env.smtp_host or "", self.env.smtp_port, timeout=30) as smtp:
            if self.env.smtp_starttls:
                smtp.starttls()
            if self.env.smtp_user:
                smtp.login(self.env.smtp_user, self.env.smtp_password or "")
            smtp.send_message(message)


class PurchaseOrderExecutor:
    """Send the order email with PDF and CSV attachments."""

    def __init__(self, mailer: Mailer, config: AppConfig, brand: Brand) -> None:
        self.mailer, self.config, self.brand = mailer, config, brand

    def execute(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        payload = action.payload
        supplier = session.get(Supplier, payload["supplier_id"])
        if supplier is None or not supplier.email:
            raise RuntimeError("Dobavljač nema e-mail adresu; narudžba nije poslana.")
        lines = load_lines(session, payload)
        delivery = (
            date.fromisoformat(payload["delivery_date"]) if payload.get("delivery_date") else None
        )
        pdf = order_pdf(
            supplier,
            lines,
            delivery,
            self.config.business.shop,
            self.brand,
            self.config.env.pdf_font_path,
            action.id,
        )

        message = EmailMessage()
        message["Subject"] = payload["subject"]
        message["From"] = self.config.env.smtp_from
        message["To"] = supplier.email
        message["Message-ID"] = make_msgid(
            domain=(self.config.env.smtp_from or "x@local").split("@")[-1]
        )
        message.set_content(payload["draft_text"])
        message.add_attachment(
            pdf, maintype="application", subtype="pdf", filename=f"narudzba-{action.id}.pdf"
        )
        message.add_attachment(
            order_csv(lines), maintype="text", subtype="csv", filename=f"narudzba-{action.id}.csv"
        )
        self.mailer.send(message)
        return {"sent_to": supplier.email, "message_id": message["Message-ID"]}

    def compensate(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        from ducan.actions.executors import NotReversible

        raise NotReversible(
            "Narudžba je već poslana dobavljaču e-mailom. Ako je treba otkazati, "
            "javi se dobavljaču izravno."
        )
