"""Margin arithmetic and price-point rounding. Prices are EUR incl. VAT."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ducan.config import PricePointTier

CENT = Decimal("0.01")


def net_price(gross: Decimal, vat_rate: Decimal) -> Decimal:
    """Selling price without VAT."""
    return gross / (1 + vat_rate / 100)


def margin_pct(gross: Decimal, vat_rate: Decimal, purchase_net: Decimal) -> Decimal:
    """Gross margin as percent of the net selling price."""
    net = net_price(gross, vat_rate)
    return (net - purchase_net) / net * 100


def min_gross_for_margin(purchase_net: Decimal, vat_rate: Decimal, floor_pct: Decimal) -> Decimal:
    """Lowest gross price that keeps ``floor_pct`` margin."""
    if floor_pct >= 100:
        raise ValueError("floor margin must be below 100 %")
    return purchase_net / (1 - floor_pct / 100) * (1 + vat_rate / 100)


def _tier_index(price: Decimal, tiers: list[PricePointTier]) -> int | None:
    for i, tier in enumerate(tiers):
        if tier.up_to is None or price <= tier.up_to:
            return i
    return None


def price_points_near(price: Decimal, tiers: list[PricePointTier]) -> list[Decimal]:
    """Allowed price points around ``price`` (a few euros either side), ascending."""
    whole = int(price)
    points = set()
    for euros in range(max(0, whole - 2), whole + 3):
        for i, tier in enumerate(tiers):
            for ending in tier.endings:
                candidate = (Decimal(euros) + ending).quantize(CENT)
                if candidate > 0 and _tier_index(candidate, tiers) == i:
                    points.add(candidate)
    return sorted(points)


def is_price_point(price: Decimal, tiers: list[PricePointTier]) -> bool:
    """True if ``price`` is an allowed price point (always true without tiers)."""
    return not tiers or price.quantize(CENT) in price_points_near(price, tiers)


def round_up(price: Decimal, tiers: list[PricePointTier]) -> Decimal:
    """Smallest allowed price point >= ``price``."""
    if not tiers:
        return price.quantize(CENT, rounding=ROUND_HALF_UP)
    return next(p for p in price_points_near(price, tiers) if p >= price)


def round_down(price: Decimal, tiers: list[PricePointTier]) -> Decimal | None:
    """Largest allowed price point <= ``price`` (None if there is none)."""
    if not tiers:
        return price.quantize(CENT)
    below = [p for p in price_points_near(price, tiers) if p <= price]
    return below[-1] if below else None
