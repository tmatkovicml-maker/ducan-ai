"""Reference validator for drafts.

Every draft that mentions a product, price, stock level or supplier must carry
references (SKU ids, supplier ids) and the values it claims. The validator
rejects the draft when:

* a referenced SKU or supplier does not exist;
* a claimed value differs from the database, or the database has no value
  (the agent may not state facts the database does not hold);
* the text names a product or supplier that is not referenced;
* the text contains a SKU-like token that is not referenced;
* the text contains a money amount or a quantity that is not one of the
  referenced values.

Known limits: numbers without a currency or unit (dates, times, phone numbers)
are not checked; product and supplier names are matched case-insensitively as
plain substrings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.models import Product, StockLevel, Supplier
from ducan.validation import ValidationResult

PRODUCT_FIELDS = {
    "name_hr",
    "unit",
    "category",
    "retail_price_gross",
    "purchase_price_net",
    "stock",
    "min_stock",
    "max_stock",
    "shelf_life_days",
}
SUPPLIER_FIELDS = {
    "name",
    "email",
    "phone",
    "lead_time_days",
    "min_order_net",
    "delivery_days",
    "payment_terms",
}
MONEY_FIELDS = {"retail_price_gross", "purchase_price_net", "min_order_net"}
QUANTITY_FIELDS = {"stock", "min_stock", "max_stock"}

_NUM = r"\d+(?:[.,]\d+)?"
MONEY_RE = re.compile(
    rf"(?:€|EUR)\s*(?P<a>{_NUM})|(?P<b>{_NUM})\s*(?:€|EUR\b|eura?\b)", re.IGNORECASE
)
QTY_RE = re.compile(
    rf"(?P<n>{_NUM})\s*(?:kom|komada|komad|kg|kutij[ae]|boc[ae]|pak|paketa|pakiranja)\b",
    re.IGNORECASE,
)
SKU_TOKEN_RE = re.compile(r"\b[A-Z]{1,5}\d{0,2}-\d{3,6}\b")


@dataclass
class Reference:
    """A pointer from a draft to a database row, with the values the draft claims."""

    kind: Literal["product", "supplier"]
    id: str
    fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Reference:
        return cls(kind=raw["kind"], id=str(raw["id"]), fields=dict(raw.get("fields") or {}))


@dataclass
class Draft:
    """Text intended for the outside world plus its references."""

    text: str
    references: list[Reference] = field(default_factory=list)


def _to_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _values_equal(db_value: Any, claimed: Any) -> bool:
    if isinstance(db_value, Decimal | int):
        claimed_dec = _to_decimal(claimed)
        return claimed_dec is not None and Decimal(db_value) == claimed_dec
    return str(db_value).strip() == str(claimed).strip()


def _product_value(session: Session, product: Product, field_name: str) -> Any:
    if field_name == "stock":
        level = session.get(StockLevel, product.sku)
        return None if level is None else level.quantity
    return getattr(product, field_name)


def validate_draft(
    session: Session,
    draft: Draft,
    extra_money: Iterable[Decimal] = (),
    extra_qty: Iterable[Decimal] = (),
) -> ValidationResult:
    """Check a draft's references and the facts in its text against the database.

    ``extra_money`` / ``extra_qty`` are values computed by code (never by the
    model), e.g. order line totals, that the text may also mention.
    """
    result = ValidationResult()
    allowed_money: set[Decimal] = set(extra_money)
    allowed_qty: set[Decimal] = set(extra_qty)
    referenced_skus: set[str] = set()
    referenced_suppliers: set[int] = set()
    referenced_names: list[str] = []

    for ref in draft.references:
        if ref.kind == "product":
            product = session.get(Product, ref.id)
            if product is None:
                result.errors.append(f"Proizvod {ref.id} ne postoji u bazi.")
                continue
            referenced_skus.add(product.sku)
            referenced_names.append(product.name_hr)
            allowed_fields = PRODUCT_FIELDS
        elif ref.kind == "supplier":
            supplier_id = int(ref.id) if str(ref.id).isdigit() else None
            supplier = None if supplier_id is None else session.get(Supplier, supplier_id)
            if supplier is None:
                result.errors.append(f"Dobavljač {ref.id} ne postoji u bazi.")
                continue
            referenced_suppliers.add(supplier.id)
            referenced_names.append(supplier.name)
            allowed_fields = SUPPLIER_FIELDS
        else:
            result.errors.append(f"Nepoznata vrsta reference: {ref.kind}.")
            continue

        for name, claimed in ref.fields.items():
            if name not in allowed_fields:
                result.errors.append(f"{ref.kind} {ref.id}: nepoznato polje '{name}'.")
                continue
            if ref.kind == "product":
                db_value = _product_value(session, product, name)
            else:
                db_value = getattr(supplier, name)
            if db_value is None:
                result.errors.append(
                    f"{ref.kind} {ref.id}: podatak '{name}' nije poznat u bazi; "
                    "ne smije se navoditi."
                )
                continue
            if not _values_equal(db_value, claimed):
                result.errors.append(
                    f"{ref.kind} {ref.id}: '{name}' u nacrtu je {claimed}, u bazi {db_value}."
                )
                continue
            if name in MONEY_FIELDS:
                allowed_money.add(Decimal(db_value))
            if name in QUANTITY_FIELDS:
                allowed_qty.add(Decimal(db_value))

    _check_text(
        session,
        draft.text,
        result,
        referenced_skus,
        referenced_suppliers,
        referenced_names,
        allowed_money,
        allowed_qty,
    )
    return result


def _check_text(
    session: Session,
    text: str,
    result: ValidationResult,
    referenced_skus: set[str],
    referenced_suppliers: set[int],
    referenced_names: list[str],
    allowed_money: set[Decimal],
    allowed_qty: set[Decimal],
) -> None:
    lowered = text.casefold()

    for product in session.scalars(select(Product)):
        if product.sku not in referenced_skus and product.name_hr.casefold() in lowered:
            result.errors.append(
                f"Tekst spominje proizvod '{product.name_hr}' bez reference ({product.sku})."
            )
    for supplier in session.scalars(select(Supplier)):
        if supplier.id not in referenced_suppliers and supplier.name.casefold() in lowered:
            result.errors.append(f"Tekst spominje dobavljača '{supplier.name}' bez reference.")

    for token in SKU_TOKEN_RE.findall(text):
        if token not in referenced_skus:
            result.errors.append(f"Tekst sadrži oznaku {token} koja nije referencirana.")

    # Names may contain sizes like "0,5 l" or "150 g"; strip them before scanning numbers.
    scrubbed = text
    for name in sorted(referenced_names, key=len, reverse=True):
        scrubbed = re.sub(re.escape(name), " ", scrubbed, flags=re.IGNORECASE)

    for match in MONEY_RE.finditer(scrubbed):
        amount = _to_decimal(match.group("a") or match.group("b"))
        if amount not in allowed_money:
            result.errors.append(
                f"Iznos {match.group(0).strip()} ne odgovara nijednoj referenciranoj cijeni."
            )
    for match in QTY_RE.finditer(scrubbed):
        qty = _to_decimal(match.group("n"))
        if qty not in allowed_qty:
            result.errors.append(
                f"Količina {match.group(0).strip()} ne odgovara nijednom referenciranom podatku."
            )
