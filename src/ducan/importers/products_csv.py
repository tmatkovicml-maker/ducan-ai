"""Read and write ``seed/products.csv`` and ``seed/suppliers.csv``."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

PRODUCT_COLUMNS = [
    "sku",
    "name_hr",
    "category",
    "unit",
    "vat_rate",
    "supplier_id",
    "purchase_price_net",
    "retail_price_gross",
    "min_stock",
    "max_stock",
    "shelf_life_days",
    "sales_share_pct",
    "active",
]
SUPPLIER_COLUMNS = [
    "id",
    "name",
    "contact_name",
    "email",
    "phone",
    "lead_time_days",
    "min_order_net",
    "delivery_days",
    "payment_terms",
    "active",
]


def read_rows(path: Path, columns: list[str]) -> list[dict[str, str]]:
    """Read a CSV and check that its header contains ``columns``."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: nedostaju stupci {', '.join(missing)}")
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def write_rows(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    """Write rows with a fixed column order."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def opt_decimal(value: str) -> Decimal | None:
    """Empty string -> None; accepts decimal comma."""
    if not value:
        return None
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"'{value}' nije broj") from exc


def opt_int(value: str) -> int | None:
    """Empty string -> None."""
    return int(value) if value else None


def parse_bool(value: str) -> bool:
    """Accept true/false, 1/0, da/ne."""
    return value.strip().lower() in {"1", "true", "da", "yes"}
