"""Sales aggregations by local shop date."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ducan.models import Sale


def local_day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    """Start and end (exclusive) of a local calendar day, as aware datetimes."""
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time.min, tzinfo=zone)
    return start, datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)


def sales_in_range(session: Session, start: date, end: date, tz: str) -> list[Sale]:
    """Sales from local date ``start`` up to and including ``end``."""
    lo, _ = local_day_bounds(start, tz)
    _, hi = local_day_bounds(end, tz)
    return list(session.scalars(select(Sale).where(Sale.ts >= lo, Sale.ts < hi)))


def units_by_sku(session: Session, start: date, end: date, tz: str) -> dict[str, Decimal]:
    """Units sold per SKU in the date range (refunds subtract)."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for sale in sales_in_range(session, start, end, tz):
        totals[sale.sku] += sale.quantity
    return dict(totals)


def first_sale_date(session: Session, tz: str) -> date | None:
    """Local date of the first recorded sale in the shop."""
    first = session.scalar(select(func.min(Sale.ts)))
    if first is None:
        return None
    # SQLite returns the raw stored value here; it is naive UTC.
    if isinstance(first, str):
        first = datetime.fromisoformat(first)
    if first.tzinfo is None:
        first = first.replace(tzinfo=ZoneInfo("UTC"))
    return first.astimezone(ZoneInfo(tz)).date()
