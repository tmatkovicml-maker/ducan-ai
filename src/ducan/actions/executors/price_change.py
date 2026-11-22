"""Executor for ``price_change``: updates the shelf price and records price history."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from ducan.models import Action, PriceHistory, Product


class StaleProposal(RuntimeError):
    """The database changed since the proposal was made."""


class PriceChangeExecutor:
    """Apply and compensate retail price changes."""

    def _set_price(
        self,
        session: Session,
        action: Action,
        actor: str,
        expected: Decimal,
        new: Decimal,
        reason: str,
    ) -> dict[str, Any]:
        product = session.get(Product, action.payload["sku"])
        if product is None:
            raise StaleProposal(f"Proizvod {action.payload['sku']} više ne postoji.")
        if product.retail_price_gross != expected:
            raise StaleProposal(
                f"Cijena za {product.sku} je u međuvremenu promijenjena "
                f"({product.retail_price_gross} €, očekivano {expected} €)."
            )
        product.retail_price_gross = new
        session.add(
            PriceHistory(
                sku=product.sku,
                field="retail_price_gross",
                old_value=expected,
                new_value=new,
                changed_by=actor,
                action_id=action.id,
                reason=reason,
            )
        )
        return {"sku": product.sku, "old": str(expected), "new": str(new)}

    def execute(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        old = Decimal(str(action.payload["old_retail_price_gross"]))
        new = Decimal(str(action.payload["new_retail_price_gross"]))
        return self._set_price(session, action, actor, old, new, action.title)

    def compensate(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        old = Decimal(str(action.payload["old_retail_price_gross"]))
        new = Decimal(str(action.payload["new_retail_price_gross"]))
        return self._set_price(session, action, actor, new, old, f"Poništenje akcije #{action.id}")
