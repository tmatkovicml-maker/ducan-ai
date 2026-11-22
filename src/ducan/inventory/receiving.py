"""Receiving goods for a purchase order (primka)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.inventory.stock import record_movement
from ducan.models import Action, ActionStatus, ActionType, MovementKind
from ducan.validation.caps import parse_lines


class ReceivingError(ValueError):
    """The order cannot be received as requested."""


def receive_order(
    session: Session, action: Action, received: dict[str, Decimal], actor: str
) -> None:
    """Book received quantities as stock receipts and close the order's open quantity."""
    if action.type != ActionType.PURCHASE_ORDER:
        raise ReceivingError("Samo narudžbe se mogu zaprimiti.")
    if action.status not in (ActionStatus.APPROVED, ActionStatus.EXECUTED):
        raise ReceivingError("Zaprimiti se može samo odobrena ili poslana narudžba.")
    if action.payload.get("received"):
        raise ReceivingError("Narudžba je već zaprimljena.")
    ordered = {line.sku: line.quantity for line in parse_lines(action.payload)}
    unknown = set(received) - set(ordered)
    if unknown:
        raise ReceivingError(f"Proizvodi nisu u narudžbi: {', '.join(sorted(unknown))}")
    for sku, qty in received.items():
        if qty < 0:
            raise ReceivingError(f"{sku}: količina ne može biti negativna.")
        if qty:
            record_movement(
                session,
                sku=sku,
                delta=qty,
                kind=MovementKind.RECEIPT,
                actor=actor,
                reason=f"Primka za narudžbu #{action.id}",
            )
    action.payload = {
        **action.payload,
        "received": True,
        "received_lines": {k: str(v) for k, v in received.items()},
    }
    differences = {
        sku: {"ordered": str(ordered[sku]), "received": str(received.get(sku, 0))}
        for sku in ordered
        if received.get(sku, Decimal("0")) != ordered[sku]
    }
    record(
        session,
        actor=actor,
        event="order.received",
        entity_type="action",
        entity_id=action.id,
        action_id=action.id,
        summary=f"Primka za narudžbu #{action.id}",
        details={"received": {k: str(v) for k, v in received.items()}, "differences": differences},
    )
