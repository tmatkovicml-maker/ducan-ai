"""Nightly replenishment: turn forecasts into purchase-order proposals per supplier."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.config import BusinessConfig
from ducan.models import Action, ActionStatus, ActionType, Product, StockLevel, Supplier
from ducan.replenishment.forecast import (
    Forecast,
    days_of_cover,
    order_quantity,
    reorder_point,
    seasonal_rate,
)
from ducan.validation.caps import committed_this_week, on_order_quantity

ZERO = Decimal("0")


@dataclass
class PlannedLine:
    """One product to order."""

    sku: str
    name: str
    unit: str
    quantity: Decimal
    unit_price_net: Decimal
    stock: Decimal
    on_order: Decimal
    per_day: Decimal
    cover_days: Decimal | None
    reorder_point: Decimal

    @property
    def total_net(self) -> Decimal:
        return self.quantity * self.unit_price_net


@dataclass
class SupplierPlan:
    """What to order from one supplier today, and why not (flags)."""

    supplier: Supplier
    delivery_date: date | None = None
    horizon_days: int = 0
    lines: list[PlannedLine] = field(default_factory=list)
    dropped: list[PlannedLine] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    proposable: bool = True

    @property
    def total_net(self) -> Decimal:
        return sum((line.total_net for line in self.lines), ZERO)


def parse_delivery_days(value: str | None) -> set[int]:
    """'2,5' -> {2, 5} (ISO weekdays)."""
    if not value:
        return set()
    return {int(part) for part in value.split(",") if part.strip()}


def next_delivery(after: date, delivery_days: set[int]) -> date:
    """First date on or after ``after`` that is a delivery day."""
    for offset in range(7):
        candidate = after + timedelta(days=offset)
        if candidate.isoweekday() in delivery_days:
            return candidate
    raise ValueError("delivery_days is empty")


def plan_supplier(
    session: Session,
    supplier: Supplier,
    forecasts: dict[str, Forecast],
    config: BusinessConfig,
    today: date,
) -> SupplierPlan:
    """Build the order for one supplier. Unknown inputs become flags, never guesses."""
    plan = SupplierPlan(supplier=supplier)
    days = parse_delivery_days(supplier.delivery_days)
    if supplier.lead_time_days is None or not days:
        plan.flags.append(
            f"{supplier.name}: nepoznat rok isporuke ili dani dostave; narudžba se ne predlaže."
        )
        plan.proposable = False
        return plan

    first = next_delivery(today + timedelta(days=supplier.lead_time_days), days)
    second = next_delivery(first + timedelta(days=1), days)
    plan.delivery_date = first
    plan.horizon_days = (second - today).days
    days_to_delivery = (first - today).days

    products = session.scalars(
        select(Product).where(Product.supplier_id == supplier.id, Product.active.is_(True))
    )
    for product in products:
        fc = forecasts.get(product.sku)
        if fc is None or fc.per_day is None:
            plan.flags.append(f"{product.sku} {product.name_hr}: nema dovoljno podataka o prodaji.")
            continue
        if product.max_stock is None or product.purchase_price_net is None:
            plan.flags.append(f"{product.sku} {product.name_hr}: nedostaje max. zaliha ili cijena.")
            continue
        per_day = seasonal_rate(fc.per_day, config, today, first)
        level = session.get(StockLevel, product.sku)
        stock = level.quantity if level else ZERO
        open_qty = on_order_quantity(session, product.sku)
        min_stock = product.min_stock or ZERO
        qty = order_quantity(
            per_day=per_day,
            stock=stock,
            on_order=open_qty,
            horizon_days=plan.horizon_days,
            days_to_delivery=days_to_delivery,
            cover_days=config.replenishment.cover_days,
            min_stock=min_stock,
            max_stock=product.max_stock,
            shelf_life_days=product.shelf_life_days,
        )
        if qty <= 0:
            continue
        plan.lines.append(
            PlannedLine(
                sku=product.sku,
                name=product.name_hr,
                unit=product.unit,
                quantity=qty,
                unit_price_net=product.purchase_price_net,
                stock=stock,
                on_order=open_qty,
                per_day=per_day,
                cover_days=days_of_cover(stock, per_day),
                reorder_point=reorder_point(per_day, plan.horizon_days, min_stock),
            )
        )

    # Most urgent first; lines are dropped from the end if a cap is hit.
    plan.lines.sort(key=lambda line: (line.cover_days is not None, line.cover_days or ZERO))
    return plan


def apply_budget(plan: SupplierPlan, session: Session, config: BusinessConfig, now) -> None:
    """Drop the least urgent lines until the order fits both spending caps."""
    caps = config.spending_caps
    if caps.per_order_net <= 0 or caps.per_week_net <= 0:
        plan.flags.append("Limiti potrošnje nisu postavljeni (0 €); narudžbe su blokirane.")
        plan.proposable = False
        return
    available = min(
        caps.per_order_net,
        caps.per_week_net - committed_this_week(session, now, config.timezone),
    )
    while plan.lines and plan.total_net > available:
        plan.dropped.append(plan.lines.pop())
    if plan.dropped:
        names = ", ".join(line.sku for line in plan.dropped)
        plan.flags.append(f"{plan.supplier.name}: zbog limita potrošnje izostavljeno: {names}.")
    minimum = plan.supplier.min_order_net
    if plan.lines and minimum is not None and plan.total_net < minimum:
        plan.flags.append(
            f"{plan.supplier.name}: narudžba {plan.total_net:.2f} € je ispod minimalne "
            f"({minimum:.2f} €); ne predlaže se."
        )
        plan.proposable = False


def pending_order(session: Session, supplier_id: int) -> Action | None:
    """An order for this supplier that still waits for approval."""
    stmt = select(Action).where(
        Action.type == ActionType.PURCHASE_ORDER, Action.status == ActionStatus.PROPOSED
    )
    return next(
        (a for a in session.scalars(stmt) if a.payload.get("supplier_id") == supplier_id), None
    )
