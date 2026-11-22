"""Import the "Asortiman" sheet of the owner's spreadsheet into ``seed/products.csv``.

The sheet has the columns: category, product, unit, purchase price excl. VAT,
VAT rate, retail price incl. VAT, share of sales. Headers are matched by the
aliases below (Croatian or English, case-insensitive). Columns that the sheet
does not have (supplier, min/max stock, shelf life) are kept from the existing
CSV for products with the same name, so the owner's additions survive a
re-import. New products get the next free ``P4-NNNN`` SKU and stay inactive
until the owner completes them.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ducan.importers.products_csv import PRODUCT_COLUMNS, read_rows, write_rows

SHEET = "Asortiman"
ALIASES: dict[str, set[str]] = {
    "category": {"kategorija", "category"},
    "name_hr": {"proizvod", "artikl", "naziv", "product"},
    "unit": {"jedinica", "jedinica mjere", "jm", "unit"},
    "purchase_price_net": {
        "nabavna cijena bez pdv-a",
        "nabavna cijena bez pdv",
        "nabavna cijena",
        "purchase price excl. vat",
        "purchase price",
    },
    "vat_rate": {"pdv", "stopa pdv-a", "pdv %", "vat rate", "vat"},
    "retail_price_gross": {
        "maloprodajna cijena s pdv-om",
        "maloprodajna cijena",
        "mpc",
        "retail price incl. vat",
        "retail price",
    },
    "sales_share_pct": {"udio u prodaji", "udio", "share of sales"},
}
SKU_RE = re.compile(r"^P4-(\d{4})$")


def _norm(header: Any) -> str:
    return re.sub(r"\s+", " ", str(header or "")).strip().casefold()


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return Decimal(str(value))
    text = str(value).strip().replace("%", "").replace("€", "").replace(",", ".").strip()
    try:
        return Decimal(text) if text else None
    except InvalidOperation as exc:
        raise ValueError(f"'{value}' nije broj") from exc


def _percent(value: Any) -> Decimal | None:
    """0.25 -> 25, 25 -> 25, '25%' -> 25."""
    dec = _to_decimal(value)
    if dec is None:
        return None
    return (dec * 100).normalize() if dec <= 1 else dec.normalize()


def _fmt(dec: Decimal | None) -> str:
    return "" if dec is None else format(dec, "f")


def read_assortment(xlsx_path: Path) -> list[dict[str, str]]:
    """Read the "Asortiman" sheet into rows keyed by product CSV column names."""
    wb = load_workbook(xlsx_path, read_only=True, data_only=True)
    if SHEET not in wb.sheetnames:
        raise ValueError(f"List '{SHEET}' ne postoji. Postojeći listovi: {wb.sheetnames}")
    rows = wb[SHEET].iter_rows(values_only=True)

    header_map: dict[int, str] | None = None
    found_headers: list[str] = []
    for raw_header in rows:
        normalized = [_norm(h) for h in raw_header]
        mapping = {
            idx: key
            for idx, h in enumerate(normalized)
            for key, names in ALIASES.items()
            if h in names
        }
        if "name_hr" in mapping.values():
            header_map = mapping
            found_headers = [h for h in normalized if h]
            break
    if header_map is None:
        raise ValueError(f"U listu '{SHEET}' nije pronađen redak zaglavlja sa stupcem 'Proizvod'.")
    missing = set(ALIASES) - set(header_map.values())
    if missing:
        raise ValueError(
            f"Nedostaju stupci: {', '.join(sorted(missing))}. Pronađeno: {found_headers}"
        )

    out = []
    for raw in rows:
        values = {key: raw[idx] if idx < len(raw) else None for idx, key in header_map.items()}
        name = str(values["name_hr"] or "").strip()
        if not name:
            continue
        out.append(
            {
                "name_hr": name,
                "category": str(values["category"] or "").strip(),
                "unit": str(values["unit"] or "").strip(),
                "purchase_price_net": _fmt(_to_decimal(values["purchase_price_net"])),
                "vat_rate": _fmt(_percent(values["vat_rate"])),
                "retail_price_gross": _fmt(_to_decimal(values["retail_price_gross"])),
                "sales_share_pct": _fmt(_percent(values["sales_share_pct"])),
            }
        )
    return out


def merge_into_csv(xlsx_path: Path, csv_path: Path) -> tuple[int, int]:
    """Merge the sheet into ``csv_path``. Returns (updated, added)."""
    existing = read_rows(csv_path, PRODUCT_COLUMNS) if csv_path.exists() else []
    by_name = {row["name_hr"].casefold(): row for row in existing}
    next_no = (
        max((int(m.group(1)) for r in existing if (m := SKU_RE.match(r["sku"]))), default=0) + 1
    )
    updated = added = 0
    for item in read_assortment(xlsx_path):
        row = by_name.get(item["name_hr"].casefold())
        if row is not None:
            row.update(item)
            updated += 1
            continue
        row = {col: "" for col in PRODUCT_COLUMNS}
        row.update(item)
        row["sku"] = f"P4-{next_no:04d}"
        row["active"] = "false"
        next_no += 1
        existing.append(row)
        by_name[item["name_hr"].casefold()] = row
        added += 1
    write_rows(csv_path, PRODUCT_COLUMNS, existing)
    return updated, added
