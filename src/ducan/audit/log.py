"""Audit log writer. Every state change goes through :func:`record`."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ducan.models import AuditLog


def record(
    session: Session,
    *,
    actor: str,
    event: str,
    summary: str = "",
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    details: dict[str, Any] | None = None,
    action_id: int | None = None,
) -> AuditLog:
    """Append an audit entry to the session (the caller commits)."""
    entry = AuditLog(
        actor=actor,
        event=event,
        summary=summary,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        details=details or {},
        action_id=action_id,
    )
    session.add(entry)
    return entry
