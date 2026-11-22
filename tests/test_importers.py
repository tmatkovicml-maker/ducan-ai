"""Assortment xlsx importer and seed loader."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from ducan.importers.assortment_xlsx import merge_into_csv, read_assortment
from ducan.importers.products_csv import PRODUCT_COLUMNS, SUPPLIER_COLUMNS, write_rows
from ducan.importers.seed import SeedError, seed_database
from ducan.models import Product, StockLevel

VAT = [Decimal("5"), Decimal("13"), Decimal("25")]


def _xlsx(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Asortiman"
    ws.append(["P4 model"])  # title row above the header
    ws.append(
        [
            "Kategorija",
            "Proizvod",
            "Jedinica",
            "Nabavna cijena bez PDV-a",
            "PDV",
            "Maloprodajna cijena s PDV-om",
            "Udio u prodaji",
        ]
    )
    ws.append(["Pića", "Voda 0,5 l", "boca", 0.4, 0.25, 1.2, 0.08])
    ws.append(["Lokalno", "Med 450 g", "staklenka", "6,50", "5%", "12,90", "3%"])
    ws.append([None, None, None, None, None, None, None])
    wb.save(path)
    return path


def test_read_assortment(tmp_path):
    rows = read_assortment(_xlsx(tmp_path / "m.xlsx"))
    assert rows[0] == {
        "name_hr": "Voda 0,5 l",
        "category": "Pića",
        "unit": "boca",
        "purchase_price_net": "0.4",
        "vat_rate": "25",
        "retail_price_gross": "1.2",
        "sales_share_pct": "8",
    }
    assert rows[1]["vat_rate"] == "5" and rows[1]["purchase_price_net"] == "6.50"
    assert len(rows) == 2


def test_missing_sheet(tmp_path):
    wb = Workbook()
    wb.save(tmp_path / "x.xlsx")
    with pytest.raises(ValueError, match="Asortiman"):
        read_assortment(tmp_path / "x.xlsx")


def test_merge_keeps_owner_columns(tmp_path):
    csv_path = tmp_path / "products.csv"
    write_rows(
        csv_path,
        PRODUCT_COLUMNS,
        [
            {
                **dict.fromkeys(PRODUCT_COLUMNS, ""),
                "sku": "P4-0007",
                "name_hr": "Voda 0,5 l",
                "category": "x",
                "unit": "boca",
                "supplier_id": "3",
                "max_stock": "100",
                "active": "true",
            }
        ],
    )
    updated, added = merge_into_csv(_xlsx(tmp_path / "m.xlsx"), csv_path)
    assert (updated, added) == (1, 1)
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    water = next(r for r in rows if r["name_hr"] == "Voda 0,5 l")
    assert water["sku"] == "P4-0007" and water["supplier_id"] == "3"
    assert water["retail_price_gross"] == "1.2"
    honey = next(r for r in rows if r["name_hr"] == "Med 450 g")
    assert honey["sku"] == "P4-0008" and honey["active"] == "false"


def _seed_dir(tmp_path: Path, product_rows: list[dict]) -> Path:
    write_rows(tmp_path / "suppliers.csv", SUPPLIER_COLUMNS, [])
    write_rows(
        tmp_path / "products.csv",
        PRODUCT_COLUMNS,
        [{**dict.fromkeys(PRODUCT_COLUMNS, ""), **r} for r in product_rows],
    )
    return tmp_path


def test_seed_inactive_products_without_prices(session, tmp_path):
    seed_dir = _seed_dir(
        tmp_path,
        [{"sku": "P4-0001", "name_hr": "Kruh", "category": "N", "unit": "kom", "active": "false"}],
    )
    assert seed_database(session, seed_dir, VAT) == (0, 1)
    assert session.get(Product, "P4-0001").retail_price_gross is None
    assert session.get(StockLevel, "P4-0001").quantity == Decimal("0")


def test_seed_rejects_active_without_prices_and_bad_vat(session, tmp_path):
    seed_dir = _seed_dir(
        tmp_path,
        [
            {"sku": "P4-0001", "name_hr": "Kruh", "category": "N", "unit": "kom", "active": "true"},
            {
                "sku": "P4-0002",
                "name_hr": "Mlijeko",
                "category": "N",
                "unit": "l",
                "vat_rate": "10",
                "active": "false",
            },
        ],
    )
    with pytest.raises(SeedError) as exc:
        seed_database(session, seed_dir, VAT)
    assert "redak 2" in str(exc.value) and "redak 3" in str(exc.value)
    assert session.get(Product, "P4-0001") is None


def test_repository_seed_files_load(session):
    seed_dir = Path(__file__).parent.parent / "seed"
    suppliers, products = seed_database(session, seed_dir, VAT)
    assert suppliers == 0 and products == 35
    assert all(not p.active for p in session.query(Product))
