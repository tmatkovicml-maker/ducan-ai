"""Classification of actions for "approve all low-risk"."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.config import LowRisk
from ducan.models import Action, ActionStatus, ActionType

LOW = "low"
NORMAL = "normal"

NEVER_LOW_RISK = {
    ActionType.SHIFT_SCHEDULE,
    ActionType.CUSTOMER_MESSAGE,
    ActionType.SOCIAL_POST,
}


def _has_executed_order(session: Session, supplier_id: Any) -> bool:
    stmt = select(Action).where(
        Action.type == ActionType.PURCHASE_ORDER, Action.status == ActionStatus.EXECUTED
    )
    return any(a.payload.get("supplier_id") == supplier_id for a in session.scalars(stmt))


def classify(
    session: Session, action_type: ActionType, payload: dict[str, Any], rules: LowRisk
) -> str:
    """Return ``"low"`` or ``"normal"``. Anything without a configured threshold is normal."""
    from ducan.validation.caps import order_total_net

    if action_type in NEVER_LOW_RISK:
        return NORMAL
    if action_type == ActionType.PURCHASE_ORDER:
        limit = rules.purchase_order_max_net
        if limit is None or not _has_executed_order(session, payload.get("supplier_id")):
            return NORMAL
        return LOW if order_total_net(payload) <= limit else NORMAL
    if action_type == ActionType.PRICE_CHANGE:
        pct = rules.price_change_max_pct
        if pct is None:
            return NORMAL
        old = Decimal(str(payload["old_retail_price_gross"]))
        new = Decimal(str(payload["new_retail_price_gross"]))
        change = abs(new - old) / old * 100
        return LOW if change <= pct else NORMAL
    if action_type == ActionType.REVIEW_REPLY:
        min_stars = rules.review_reply_min_stars
        stars = payload.get("stars")
        if min_stars is None or not isinstance(stars, int):
            return NORMAL
        return LOW if stars >= min_stars else NORMAL
    return NORMAL
