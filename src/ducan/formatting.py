"""Croatian number formatting shared by the UI and documents."""

from __future__ import annotations

from decimal import Decimal

UNKNOWN = "nepoznato"


def fmt_money(value: Decimal | None) -> str:
    """Croatian money format: 1.234,50 €."""
    if value is None:
        return UNKNOWN
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{text} €"


def fmt_num(value: Decimal | int | None) -> str:
    """Decimal with a comma and no trailing zeros."""
    if value is None:
        return UNKNOWN
    dec = Decimal(value)
    text = format(dec.normalize(), "f") if dec == dec.to_integral() else format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")
