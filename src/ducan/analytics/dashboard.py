"""Numbers for the dashboard: sales, margin, stock cover, expiring stock, what to order."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.analytics.sales import sales_in_range
from ducan.config import BusinessConfig
from ducan.models import JobRun, MovementKind, Product, StockLevel, StockMovement
from ducan.replenishment.forecast import days_of_cover, forecast_products

ZERO = Decimal("0")


@dataclass
class DayTotal:
    day: date
    gross: Decimal


@dataclass
class CategoryTotal:
    category: str
    gross: Decimal = ZERO
    net: Decimal = ZERO
    cost: Decimal = ZERO
    cost_unknown: bool = False

    @property
    def margin(self) -> Decimal:
        return self.net - self.cost

    @property
    def margin_pct(self) -> Decimal | None:
        return None if not self.net else self.margin / self.net * 100


@dataclass
class CoverRow:
    product: Product
    stock: Decimal
    per_day: Decimal | None
    cover_days: Decimal | None


@dataclass
class ExpiryRow:
    product: Product
    quantity: Decimal
    expires_on: date


def sales_by_day(session: Session, start: date, end: date, tz: str) -> list[DayTotal]:
    """Gross sales per local day, including days without sales."""
    zone = ZoneInfo(tz)
    totals: dict[date, Decimal] = defaultdict(Decimal)
    for sale in sales_in_range(session, start, end, tz):
        totals[sale.ts.astimezone(zone).date()] += sale.gross_amount
    days = (end - start).days + 1
    return [
        DayTotal(start + timedelta(days=i), totals.get(start + timedelta(days=i), ZERO))
        for i in range(days)
    ]


def sales_by_category(session: Session, start: date, end: date, tz: str) -> list[CategoryTotal]:
    """Gross, net, cost of goods (at current purchase price) and margin per category."""
    products = {p.sku: p for p in session.scalars(select(Product))}
    totals: dict[str, CategoryTotal] = {}
    for sale in sales_in_range(session, start, end, tz):
        product = products[sale.sku]
        row = totals.setdefault(product.category, CategoryTotal(product.category))
        row.gross += sale.gross_amount
        if product.vat_rate is None or product.purchase_price_net is None:
            row.cost_unknown = True
            continue
        row.net += sale.gross_amount / (1 + product.vat_rate / 100)
        row.cost += sale.quantity * product.purchase_price_net
    return sorted(totals.values(), key=lambda r: r.gross, reverse=True)


def stock_cover(session: Session, config: BusinessConfig, today: date) -> list[CoverRow]:
    """Active products by days of cover, shortest first; unknown demand last."""
    products = list(session.scalars(select(Product).where(Product.active.is_(True))))
    forecasts = forecast_products(session, products, config, today)
    rows = []
    for product in products:
        level = session.get(StockLevel, product.sku)
        stock = level.quantity if level else ZERO
        per_day = forecasts[product.sku].per_day
        rows.append(CoverRow(product, stock, per_day, days_of_cover(stock, per_day)))
    return sorted(rows, key=lambda r: (r.cover_days is None, r.cover_days or ZERO))


def expiring(session: Session, tz: str, today: date, within_days: int) -> list[ExpiryRow]:
    """Stock that expires within ``within_days``, estimated first-in-first-out.

    Without batch tracking, on-hand stock is assumed to come from the most recent
    receipts. Stock not covered by any receipt has no known expiry and is skipped.
    """
    zone = ZoneInfo(tz)
    rows = []
    products = session.scalars(select(Product).where(Product.shelf_life_days.is_not(None)))
    for product in products:
        level = session.get(StockLevel, product.sku)
        remaining = level.quantity if level else ZERO
        if remaining <= 0:
            continue
        receipts = session.scalars(
            select(StockMovement)
            .where(StockMovement.sku == product.sku, StockMovement.kind == MovementKind.RECEIPT)
            .order_by(StockMovement.created_at.desc())
        )
        for receipt in receipts:
            if remaining <= 0:
                break
            qty = min(receipt.delta, remaining)
            remaining -= qty
            expires = receipt.created_at.astimezone(zone).date() + timedelta(
                days=product.shelf_life_days or 0
            )
            if expires <= today + timedelta(days=within_days):
                rows.append(ExpiryRow(product, qty, expires))
    return sorted(rows, key=lambda r: r.expires_on)


def latest_run(session: Session, job: str) -> JobRun | None:
    """Most recent run of a job."""
    return session.scalar(
        select(JobRun).where(JobRun.job == job).order_by(JobRun.id.desc()).limit(1)
    )
