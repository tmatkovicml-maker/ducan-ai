"""Validation of price-change proposals."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from ducan.config import Pricing
from ducan.models import Product
from ducan.pricing.rules import is_price_point, margin_pct
from ducan.validation import ValidationResult


def validate_price_change(
    session: Session, payload: dict[str, Any], pricing: Pricing | None = None
) -> ValidationResult:
    """The product must exist, the old price must match the database, the new must be sane.

    With pricing rules, the new price must also keep the floor margin and be an
    allowed price point.
    """
    result = ValidationResult()
    sku = payload.get("sku")
    product = session.get(Product, sku) if sku else None
    if product is None:
        result.errors.append(f"Proizvod {sku} ne postoji.")
        return result
    try:
        old = Decimal(str(payload["old_retail_price_gross"]))
        new = Decimal(str(payload["new_retail_price_gross"]))
    except (KeyError, InvalidOperation):
        result.errors.append("Promjena cijene mora imati staru i novu cijenu.")
        return result
    if product.retail_price_gross is None:
        result.errors.append(f"{sku}: trenutna cijena nije poznata.")
    elif old != product.retail_price_gross:
        result.errors.append(
            f"{sku}: stara cijena u prijedlogu {old} € ne odgovara bazi "
            f"{product.retail_price_gross} €."
        )
    if new <= 0 or new.as_tuple().exponent < -2:
        result.errors.append(f"{sku}: nova cijena {new} nije ispravan iznos u eurima.")
        return result
    if new == old:
        result.errors.append(f"{sku}: nova cijena je jednaka staroj.")
    if pricing is None:
        return result
    floor = pricing.floor_for(product.category)
    if floor is not None and product.vat_rate is not None and product.purchase_price_net:
        margin = margin_pct(new, product.vat_rate, product.purchase_price_net)
        if margin < floor:
            result.errors.append(
                f"{sku}: uz cijenu {new} € marža bi bila {margin:.1f} %, ispod minimalne {floor} %."
            )
    if not is_price_point(new, pricing.price_points):
        result.errors.append(f"{sku}: {new} € nije dopuštena cjenovna točka.")
    return result
