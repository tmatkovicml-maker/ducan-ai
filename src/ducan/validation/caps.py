"""Purchase-order validation: references, stock rules and hard spending caps.

Anything above a cap is blocked. A cap of 0 blocks every order, which is the
state until the owner sets real values in ``config/settings.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.config import BusinessConfig
from ducan.models import Action, ActionStatus, ActionType, Product, StockLevel, Supplier
from ducan.validation import ValidationResult

COMMITTED_STATUSES = (ActionStatus.APPROVED, ActionStatus.EXECUTED)


@dataclass(frozen=True)
class OrderLine:
    """One line of a purchase order."""

    sku: str
    quantity: Decimal
    unit_price_net: Decimal

    @property
    def total_net(self) -> Decimal:
        return self.quantity * self.unit_price_net


def parse_lines(payload: dict[str, Any]) -> list[OrderLine]:
    """Parse order lines from an action payload; raises ``ValueError`` on bad input."""
    lines = []
    for raw in payload.get("lines") or []:
        try:
            lines.append(
                OrderLine(
                    sku=str(raw["sku"]),
                    quantity=Decimal(str(raw["quantity"])),
                    unit_price_net=Decimal(str(raw["unit_price_net"])),
                )
            )
        except (KeyError, InvalidOperation) as exc:
            raise ValueError(f"Neispravna stavka narudžbe: {raw!r}") from exc
    return lines


def order_total_net(payload: dict[str, Any]) -> Decimal:
    """Total net value of an order payload."""
    return sum((line.total_net for line in parse_lines(payload)), Decimal("0"))


def week_bounds(now: datetime, tz: str) -> tuple[datetime, datetime]:
    """Start (Monday 00:00 local) and end of the ISO week containing ``now``."""
    local = now.astimezone(ZoneInfo(tz))
    start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start, start + timedelta(days=7)


def committed_this_week(
    session: Session, now: datetime, tz: str, exclude_action_id: int | None = None
) -> Decimal:
    """Net value of purchase orders approved or executed in the current week."""
    start, end = week_bounds(now, tz)
    total = Decimal("0")
    stmt = select(Action).where(
        Action.type == ActionType.PURCHASE_ORDER, Action.status.in_(COMMITTED_STATUSES)
    )
    for action in session.scalars(stmt):
        if action.id == exclude_action_id or action.decided_at is None:
            continue
        if start <= action.decided_at < end:
            total += order_total_net(action.payload)
    return total


def on_order_quantity(session: Session, sku: str, exclude_action_id: int | None = None) -> Decimal:
    """Quantity of ``sku`` in approved/executed orders not yet marked received."""
    qty = Decimal("0")
    stmt = select(Action).where(
        Action.type == ActionType.PURCHASE_ORDER, Action.status.in_(COMMITTED_STATUSES)
    )
    for action in session.scalars(stmt):
        if action.id == exclude_action_id or action.payload.get("received"):
            continue
        for line in parse_lines(action.payload):
            if line.sku == sku:
                qty += line.quantity
    return qty


def validate_purchase_order(
    session: Session,
    payload: dict[str, Any],
    config: BusinessConfig,
    now: datetime,
    exclude_action_id: int | None = None,
) -> ValidationResult:
    """Validate a purchase order. Errors block it; there is no override."""
    result = ValidationResult()
    supplier_id = payload.get("supplier_id")
    supplier = session.get(Supplier, supplier_id) if isinstance(supplier_id, int) else None
    if supplier is None:
        result.errors.append(f"Dobavljač {supplier_id} ne postoji.")
        return result
    if not supplier.active:
        result.errors.append(f"Dobavljač {supplier.name} nije aktivan.")

    try:
        lines = parse_lines(payload)
    except ValueError as exc:
        result.errors.append(str(exc))
        return result
    if not lines:
        result.errors.append("Narudžba nema stavki.")
        return result

    seen: set[str] = set()
    for line in lines:
        _validate_line(session, line, supplier, seen, result, exclude_action_id)

    total = sum((line.total_net for line in lines), Decimal("0"))
    caps = config.spending_caps
    if caps.per_order_net <= 0:
        result.errors.append("Limit po narudžbi nije postavljen (0 €); narudžbe su blokirane.")
    elif total > caps.per_order_net:
        result.errors.append(
            f"Narudžba ({total:.2f} € neto) prelazi limit po narudžbi "
            f"({caps.per_order_net:.2f} € neto)."
        )
    if caps.per_week_net <= 0:
        result.errors.append("Tjedni limit nije postavljen (0 €); narudžbe su blokirane.")
    else:
        week_total = committed_this_week(session, now, config.timezone, exclude_action_id) + total
        if week_total > caps.per_week_net:
            result.errors.append(
                f"Tjedni iznos narudžbi bio bi {week_total:.2f} € neto, a limit je "
                f"{caps.per_week_net:.2f} € neto."
            )

    if supplier.min_order_net is None:
        result.warnings.append(f"Minimalna narudžba za {supplier.name} nije poznata.")
    elif total < supplier.min_order_net:
        result.errors.append(
            f"Narudžba ({total:.2f} € neto) je ispod minimalne narudžbe dobavljača "
            f"({supplier.min_order_net:.2f} € neto)."
        )
    return result


def _validate_line(
    session: Session,
    line: OrderLine,
    supplier: Supplier,
    seen: set[str],
    result: ValidationResult,
    exclude_action_id: int | None,
) -> None:
    if line.sku in seen:
        result.errors.append(f"{line.sku} se pojavljuje više puta u narudžbi.")
    seen.add(line.sku)
    product = session.get(Product, line.sku)
    if product is None:
        result.errors.append(f"Proizvod {line.sku} ne postoji.")
        return
    if not product.active:
        result.errors.append(f"Proizvod {line.sku} nije aktivan.")
    if product.supplier_id != supplier.id:
        result.errors.append(f"Proizvod {line.sku} nije vezan uz dobavljača {supplier.name}.")
    if line.quantity <= 0:
        result.errors.append(f"{line.sku}: količina mora biti veća od 0.")
    if product.purchase_price_net is None:
        result.errors.append(f"{line.sku}: nabavna cijena nije poznata.")
    elif line.unit_price_net != product.purchase_price_net:
        result.errors.append(
            f"{line.sku}: cijena u narudžbi {line.unit_price_net} € ne odgovara nabavnoj "
            f"cijeni u bazi {product.purchase_price_net} €."
        )
    if product.max_stock is None:
        result.errors.append(f"{line.sku}: maksimalna zaliha nije postavljena.")
        return
    level = session.get(StockLevel, line.sku)
    current = level.quantity if level else Decimal("0")
    on_order = on_order_quantity(session, line.sku, exclude_action_id)
    if current + on_order + line.quantity > product.max_stock:
        result.errors.append(
            f"{line.sku}: zaliha {current} + naručeno {on_order} + {line.quantity} "
            f"prelazi maksimalnu zalihu {product.max_stock}."
        )
