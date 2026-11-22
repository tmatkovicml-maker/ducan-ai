"""CSV POS import: idempotent, no personal data, unknown SKUs rejected."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from ducan.models import Sale, SalesImport, StockLevel, StockMovement
from ducan.pos.api_pos import ApiPos
from ducan.pos.csv_pos import CsvPos, parse_csv
from ducan.pos.importer import import_batch, import_from_adapter

CSV = (
    "timestamp;sku;quantity;gross_amount;payment_method;card_number;customer\n"
    "2026-07-15T10:15:00;P4-0001;2;2,40;kartica;4111111111111111;Ivo Ivić\n"
    "2026-07-15T10:20:00+02:00;P4-0002;1;15.90;cash;;\n"
    "2026-07-15T10:25:00;P4-9999;1;1.00;cash;;\n"
    "nije-datum;P4-0001;1;1.20;cash;;\n"
    "2026-07-15T11:00:00;P4-0001;-1;-1.20;cash;;\n"
).encode()


def test_parse_ignores_extra_columns():
    batch = parse_csv(CSV, name="d.csv", tz="Europe/Zagreb")
    assert batch.ignored_columns == ["card_number", "customer"]
    assert len(batch.rows) == 4
    assert batch.errors == [{"row": 5, "error": batch.errors[0]["error"]}]
    first = batch.rows[0][1]
    assert first.ts.utcoffset().total_seconds() == 7200  # local summer time
    assert first.gross_amount == Decimal("2.40")


def test_import_stores_sales_and_movements(catalog):
    outcome = import_batch(catalog, parse_csv(CSV, name="d.csv", tz="Europe/Zagreb"))
    assert (outcome.status, outcome.rows_ok, outcome.rows_rejected) == ("imported", 3, 2)
    assert catalog.get(StockLevel, "P4-0001").quantity == Decimal("29")  # 30 - 2 + 1 refund
    assert catalog.get(StockLevel, "P4-0002").quantity == Decimal("4")
    assert len(list(catalog.scalars(select(StockMovement)))) == 3
    imp = catalog.scalar(select(SalesImport))
    assert [e["row"] for e in imp.errors] == [4, 5]


def test_no_personal_data_is_stored(catalog):
    import_batch(catalog, parse_csv(CSV, name="d.csv", tz="Europe/Zagreb"))
    catalog.commit()
    conn = catalog.connection()
    dump = "".join(
        str(conn.exec_driver_sql(f"SELECT * FROM {table}").fetchall())
        for table in ("sales", "sales_imports", "stock_movements", "audit_log")
    )
    assert "4111111111111111" not in dump
    assert "Ivo" not in dump


def test_same_file_is_imported_once(catalog):
    batch = parse_csv(CSV, name="d.csv", tz="Europe/Zagreb")
    import_batch(catalog, batch)
    again = import_batch(catalog, parse_csv(CSV, name="kopija.csv", tz="Europe/Zagreb"))
    assert again.status == "duplicate"
    assert len(list(catalog.scalars(select(Sale)))) == 3


def test_missing_columns_reject_file(catalog):
    batch = parse_csv(b"sku,quantity\nP4-0001,1\n", name="x.csv", tz="Europe/Zagreb")
    assert import_batch(catalog, batch).status == "rejected"


def test_watched_folder(tmp_path, catalog):
    inbox = tmp_path / "in"
    adapter = CsvPos(inbox, tmp_path / "arch", tmp_path / "rej", "Europe/Zagreb")
    (inbox / "2026-07-15.csv").write_bytes(CSV)
    (inbox / "lose.csv").write_bytes(b"x,y\n1,2\n")
    outcomes = import_from_adapter(catalog, adapter)
    assert sorted(o.status for o in outcomes) == ["imported", "rejected"]
    assert (tmp_path / "arch" / "2026-07-15.csv").exists()
    assert (tmp_path / "rej" / "lose.csv").exists()
    assert not list(inbox.iterdir())


def test_api_pos_is_a_stub():
    with pytest.raises(NotImplementedError):
        list(ApiPos().fetch())
