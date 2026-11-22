"""Approval queue and audit log."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ducan.db import Base, UTCDateTime, utcnow


class ActionType(StrEnum):
    """Every kind of outward action the agent may propose."""

    PURCHASE_ORDER = "purchase_order"
    PRICE_CHANGE = "price_change"
    SHIFT_SCHEDULE = "shift_schedule"
    SOCIAL_POST = "social_post"
    REVIEW_REPLY = "review_reply"
    CUSTOMER_MESSAGE = "customer_message"


class ActionStatus(StrEnum):
    """Lifecycle of an action; see ``ducan.actions.state_machine``."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class Action(Base):
    """A proposed side effect waiting for (or past) human approval."""

    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default=ActionStatus.PROPOSED)
    title: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Machine-checkable references: [{"kind": "product", "id": "P4-0001", "fields": {...}}]
    references: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    reasoning_summary: Mapped[str] = mapped_column(Text, default="")
    data_used: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk: Mapped[str] = mapped_column(String(16), default="normal")
    proposed_by: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decided_by: Mapped[str | None] = mapped_column(String(50))
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    rolled_back_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rollback_reason: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """Append-only record of who did what, when, and why."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(50))  # owner | agent | system
    event: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("actions.id"))
    # Set on LLM calls only.
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
