"""Sell-through, forecast and reorder-point calculation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ducan.config import DemandPrior
from ducan.models import Product
from ducan.replenishment.forecast import (
    days_of_cover,
    forecast_products,
    order_quantity,
    reorder_point,
    seasonal_rate,
)
from tests.conftest import add_daily_sales

TODAY = date(2026, 11, 18)
D = Decimal


def test_reorder_point():
    # 10/day for 6 days until the next-but-one delivery, plus 24 safety stock.
    assert reorder_point(D("10"), 6, D("24")) == D("84")


def _qty(**overrides):
    args = dict(
        per_day=D("10"),
        stock=D("30"),
        on_order=D("0"),
        horizon_days=6,
        days_to_delivery=2,
        cover_days=3,
        min_stock=D("24"),
        max_stock=D("240"),
        shelf_life_days=None,
    )
    args.update(overrides)
    return order_quantity(**args)


def test_order_up_to_target():
    # target = 24 + 10 * (6 + 3) = 114; position 30 -> order 84
    assert _qty() == D("84")


def test_no_order_above_reorder_point():
    assert _qty(stock=D("84")) == 0
    assert _qty(stock=D("50"), on_order=D("40")) == 0


def test_rounds_up_fractional_demand():
    # target = 4 + 0.5 * 9 = 8.5, position 5 -> 3.5 -> 4
    assert _qty(per_day=D("0.5"), stock=D("5"), min_stock=D("4"), max_stock=D("20")) == D("4")


def test_capped_by_max_stock():
    assert _qty(max_stock=D("100")) == D("70")


def test_capped_by_shelf_life():
    # Sellable before expiry: 10 * (2 + 5) = 70 -> at most 40 more on top of 30.
    assert _qty(shelf_life_days=5) == D("40")


def test_forecast_uses_weighted_averages(catalog, business):
    add_daily_sales(catalog, "P4-0001", "10", 28, TODAY)
    # Last 14 days were busier: +10/day.
    add_daily_sales(catalog, "P4-0001", "10", 14, TODAY)
    product = catalog.get(Product, "P4-0001")
    fc = forecast_products(catalog, [product], business, TODAY)["P4-0001"]
    assert fc.avg_14d == D("20") and fc.avg_28d == D("15")
    assert fc.per_day == D("0.6") * 20 + D("0.4") * 15  # 18
    assert days_of_cover(D("36"), fc.per_day) == D("2")


def test_short_history_without_prior_is_unknown(catalog, business):
    add_daily_sales(catalog, "P4-0001", "10", 3, TODAY)
    fc = forecast_products(catalog, [catalog.get(Product, "P4-0001")], business, TODAY)
    assert fc["P4-0001"].per_day is None


def test_prior_from_share_of_sales(catalog, business):
    product = catalog.get(Product, "P4-0001")
    product.sales_share_pct = D("6")
    business.demand_prior = DemandPrior(expected_daily_revenue_gross=D("400"))
    # No history: 400 * 6 % / 1.20 = 20 units/day
    fc = forecast_products(catalog, [product], business, TODAY)
    assert fc["P4-0001"].per_day == D("20")


def test_seasonal_factor(business):
    business.seasonality = {11: D("0.5"), 12: D("1.5")}
    assert seasonal_rate(D("10"), business, TODAY, date(2026, 12, 2)) == D("30")
