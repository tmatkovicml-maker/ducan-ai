"""Load ``seed/suppliers.csv`` and ``seed/products.csv`` into the database (upsert)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.importers.products_csv import (
    PRODUCT_COLUMNS,
    SUPPLIER_COLUMNS,
    opt_decimal,
    opt_int,
    parse_bool,
    read_rows,
)
from ducan.models import Product, StockLevel, Supplier


class SeedError(ValueError):
    """Seed data is invalid; nothing was written."""


def validate_product_fields(
    *,
    active: bool,
    vat_rate: Decimal | None,
    purchase: Decimal | None,
    retail: Decimal | None,
    min_stock: Decimal | None,
    max_stock: Decimal | None,
    vat_rates: list[Decimal],
) -> list[str]:
    """Rules shared by the seed loader and the admin forms."""
    errors = []
    if vat_rate is not None and vat_rate not in vat_rates:
        errors.append(f"stopa PDV-a {vat_rate} nije među dopuštenima {vat_rates}")
    if active and (vat_rate is None or purchase is None or retail is None):
        errors.append("aktivan proizvod mora imati PDV, nabavnu i maloprodajnu cijenu")
    for label, value in (("nabavna cijena", purchase), ("maloprodajna cijena", retail)):
        if value is not None and value <= 0:
            errors.append(f"{label} mora biti veća od 0")
    if min_stock is not None and max_stock is not None and min_stock > max_stock:
        errors.append("minimalna zaliha je veća od maksimalne")
    return errors


def seed_database(
    session: Session, seed_dir: Path, vat_rates: list[Decimal], actor: str = "owner"
) -> tuple[int, int]:
    """Upsert suppliers and products. Returns (suppliers, products) loaded."""
    errors: list[str] = []
    supplier_rows = read_rows(seed_dir / "suppliers.csv", SUPPLIER_COLUMNS)
    product_rows = read_rows(seed_dir / "products.csv", PRODUCT_COLUMNS)

    supplier_ids = set()
    for line, row in enumerate(supplier_rows, start=2):
        try:
            sid = int(row["id"])
            supplier = session.get(Supplier, sid) or Supplier(id=sid)
            supplier.name = row["name"]
            supplier.contact_name = row["contact_name"] or None
            supplier.email = row["email"] or None
            supplier.phone = row["phone"] or None
            supplier.lead_time_days = opt_int(row["lead_time_days"])
            supplier.min_order_net = opt_decimal(row["min_order_net"])
            supplier.delivery_days = row["delivery_days"] or None
            supplier.payment_terms = row["payment_terms"] or None
            supplier.active = parse_bool(row["active"] or "true")
            if not supplier.name:
                raise ValueError("naziv je obavezan")
            session.add(supplier)
            supplier_ids.add(sid)
        except ValueError as exc:
            errors.append(f"suppliers.csv redak {line}: {exc}")

    for line, row in enumerate(product_rows, start=2):
        try:
            sku = row["sku"]
            if not sku or not row["name_hr"]:
                raise ValueError("SKU i naziv su obavezni")
            supplier_id = opt_int(row["supplier_id"])
            if (
                supplier_id is not None
                and supplier_id not in supplier_ids
                and session.get(Supplier, supplier_id) is None
            ):
                raise ValueError(f"dobavljač {supplier_id} ne postoji")
            product = session.get(Product, sku) or Product(sku=sku)
            product.name_hr = row["name_hr"]
            product.category = row["category"]
            product.unit = row["unit"]
            product.vat_rate = opt_decimal(row["vat_rate"])
            product.supplier_id = supplier_id
            product.purchase_price_net = opt_decimal(row["purchase_price_net"])
            product.retail_price_gross = opt_decimal(row["retail_price_gross"])
            product.min_stock = opt_decimal(row["min_stock"])
            product.max_stock = opt_decimal(row["max_stock"])
            product.shelf_life_days = opt_int(row["shelf_life_days"])
            product.sales_share_pct = opt_decimal(row["sales_share_pct"])
            product.active = parse_bool(row["active"])
            problems = validate_product_fields(
                active=product.active,
                vat_rate=product.vat_rate,
                purchase=product.purchase_price_net,
                retail=product.retail_price_gross,
                min_stock=product.min_stock,
                max_stock=product.max_stock,
                vat_rates=vat_rates,
            )
            if problems:
                raise ValueError("; ".join(problems))
            session.add(product)
            if session.get(StockLevel, sku) is None:
                session.add(StockLevel(sku=sku, quantity=Decimal("0")))
        except ValueError as exc:
            errors.append(f"products.csv redak {line}: {exc}")

    if errors:
        session.rollback()
        raise SeedError("\n".join(errors))
    record(
        session,
        actor=actor,
        event="seed.loaded",
        entity_type="seed",
        summary=f"Učitano {len(supplier_rows)} dobavljača i {len(product_rows)} proizvoda",
    )
    return len(supplier_rows), len(product_rows)
