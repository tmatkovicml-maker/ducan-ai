"""Helpers for parsing HTML form values."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


class FormError(ValueError):
    """A form field has an invalid value."""


def dec(value: str | None, label: str) -> Decimal | None:
    """Parse an optional decimal (comma or point)."""
    if value is None or not value.strip():
        return None
    try:
        result = Decimal(value.strip().replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise FormError(f"{label}: '{value}' nije broj.") from exc
    if not result.is_finite():
        raise FormError(f"{label}: '{value}' nije broj.")
    return result


def integer(value: str | None, label: str) -> int | None:
    """Parse an optional integer."""
    if value is None or not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError as exc:
        raise FormError(f"{label}: '{value}' nije cijeli broj.") from exc


def text(value: str | None) -> str | None:
    """Strip; empty -> None."""
    value = (value or "").strip()
    return value or None
