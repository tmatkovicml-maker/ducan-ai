"""Stock movements. All stock changes go through :func:`record_movement`."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from ducan.db import utcnow
from ducan.models import MovementKind, StockLevel, StockMovement


def record_movement(
    session: Session,
    *,
    sku: str,
    delta: Decimal,
    kind: MovementKind,
    actor: str,
    reason: str | None = None,
    sale_id: int | None = None,
) -> StockMovement:
    """Append a movement and update the stock level in the same transaction."""
    movement = StockMovement(
        sku=sku, delta=delta, kind=kind, reason=reason, sale_id=sale_id, created_by=actor
    )
    session.add(movement)
    level = session.get(StockLevel, sku)
    if level is None:
        level = StockLevel(sku=sku, quantity=Decimal("0"))
        session.add(level)
    level.quantity = (level.quantity or Decimal("0")) + delta
    level.updated_at = utcnow()
    return movement
