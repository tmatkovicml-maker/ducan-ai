"""Sell-through, demand forecast, days of cover and reorder point.

forecast per day = blended sales rate (this month); for a later date it is scaled by
seasonal factor(target month) / factor(this month), see :func:`seasonal_rate`.

* sales rate = w14 x avg(last 14 days) + w28 x avg(last 28 days), where each
  average divides by the days the shop has actually been selling (max 14/28);
* until ``min_history_days`` of history exist, a prior from the owner's
  "share of sales" and expected turnover is blended in (if configured);
* with neither, the forecast is unknown and the product is flagged, not guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from sqlalchemy.orm import Session

from ducan.analytics.sales import first_sale_date, units_by_sku
from ducan.config import BusinessConfig
from ducan.models import Product

ZERO = Decimal("0")


@dataclass(frozen=True)
class Forecast:
    """Demand estimate for one SKU, with the inputs that produced it."""

    sku: str
    avg_14d: Decimal | None
    avg_28d: Decimal | None
    history_days: int
    prior_per_day: Decimal | None
    season_factor: Decimal
    per_day: Decimal | None  # None = unknown

    def as_dict(self) -> dict[str, str | int | None]:
        def s(v: Decimal | None) -> str | None:
            return None if v is None else str(v.quantize(Decimal("0.001")))

        return {
            "avg_14d": s(self.avg_14d),
            "avg_28d": s(self.avg_28d),
            "history_days": self.history_days,
            "prior_per_day": s(self.prior_per_day),
            "season_factor": str(self.season_factor),
            "per_day": s(self.per_day),
        }


def prior_per_day(product: Product, config: BusinessConfig) -> Decimal | None:
    """Units/day implied by share of sales and expected daily turnover, if both are known."""
    revenue = config.demand_prior.expected_daily_revenue_gross
    if revenue is None or product.sales_share_pct is None or not product.retail_price_gross:
        return None
    return revenue * product.sales_share_pct / 100 / product.retail_price_gross


def forecast_products(
    session: Session, products: list[Product], config: BusinessConfig, today: date
) -> dict[str, Forecast]:
    """Forecast every product, using sales up to and including yesterday."""
    tz = config.timezone
    yesterday = today - timedelta(days=1)
    units_14 = units_by_sku(session, today - timedelta(days=14), yesterday, tz)
    units_28 = units_by_sku(session, today - timedelta(days=28), yesterday, tz)
    first = first_sale_date(session, tz)
    history = 0 if first is None else max(0, (today - first).days)
    days_14, days_28 = min(14, history), min(28, history)
    rep = config.replenishment
    factor = config.season_factor(today.month)
    result = {}
    for product in products:
        avg14 = units_14.get(product.sku, ZERO) / days_14 if days_14 else None
        avg28 = units_28.get(product.sku, ZERO) / days_28 if days_28 else None
        prior = prior_per_day(product, config)
        rate: Decimal | None = None
        if avg14 is not None and avg28 is not None:
            rate = rep.weight_14d * avg14 + rep.weight_28d * avg28
        if history < rep.min_history_days:
            if prior is not None and rate is not None:
                w = Decimal(history) / Decimal(rep.min_history_days)
                rate = w * rate + (1 - w) * prior
            elif prior is not None:
                rate = prior
            else:
                rate = None  # not enough history and no prior: unknown
        per_day = None if rate is None else max(rate, ZERO)
        result[product.sku] = Forecast(product.sku, avg14, avg28, history, prior, factor, per_day)
    return result


def seasonal_rate(rate: Decimal, config: BusinessConfig, today: date, target: date) -> Decimal:
    """Scale a daily rate measured around ``today`` to the month of ``target``."""
    return rate * config.season_factor(target.month) / config.season_factor(today.month)


def days_of_cover(stock: Decimal, per_day: Decimal | None) -> Decimal | None:
    """How many days current stock lasts; None if demand is unknown or zero."""
    if per_day is None or per_day <= 0:
        return None
    return max(stock, ZERO) / per_day


def reorder_point(per_day: Decimal, horizon_days: int, min_stock: Decimal) -> Decimal:
    """Stock (incl. open orders) below which we must order now.

    ``horizon_days`` is the time until the next-but-one possible delivery: if we
    do not order today, stock has to last that long and still keep ``min_stock``.
    """
    return per_day * horizon_days + min_stock


def order_quantity(
    *,
    per_day: Decimal,
    stock: Decimal,
    on_order: Decimal,
    horizon_days: int,
    days_to_delivery: int,
    cover_days: int,
    min_stock: Decimal,
    max_stock: Decimal,
    shelf_life_days: int | None,
) -> Decimal:
    """Whole units to order (0 if none), honouring max stock and shelf life."""
    position = stock + on_order
    if position >= reorder_point(per_day, horizon_days, min_stock):
        return ZERO
    target = min_stock + per_day * (horizon_days + cover_days)
    qty = (target - position).to_integral_value(rounding=ROUND_CEILING)
    limit = max_stock - position
    if shelf_life_days is not None:
        # Do not hold more than can be sold before it expires.
        sellable = per_day * (days_to_delivery + shelf_life_days)
        limit = min(limit, sellable - position)
    limit = limit.to_integral_value(rounding=ROUND_FLOOR)
    return max(ZERO, min(qty, limit))
