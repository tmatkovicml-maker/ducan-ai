"""Validation of price-change proposals."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from ducan.models import Product
from ducan.validation import ValidationResult


def validate_price_change(session: Session, payload: dict[str, Any]) -> ValidationResult:
    """The product must exist, the old price must match the database, the new must be sane."""
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
    if new == old:
        result.errors.append(f"{sku}: nova cijena je jednaka staroj.")
    return result
