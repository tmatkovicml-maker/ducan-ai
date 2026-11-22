"""Supplier email, CSV and PDF order sheet, all rendered from the database."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fpdf import FPDF
from sqlalchemy.orm import Session

from ducan.brand import Brand
from ducan.comms.disclosure import DISCLOSURE_HR
from ducan.config import ShopInfo
from ducan.formatting import fmt_money, fmt_num
from ducan.models import Product, Supplier

ORDER_NOTE = "Ovu narudžbu pripremio je AI sustav, a odobrio vlasnik."


class DocumentError(RuntimeError):
    """A document cannot be produced (e.g. missing font)."""


@dataclass(frozen=True)
class OrderLineView:
    """A line as shown in documents."""

    sku: str
    name: str
    unit: str
    quantity: Decimal
    unit_price_net: Decimal

    @property
    def total_net(self) -> Decimal:
        return self.quantity * self.unit_price_net


def load_lines(session: Session, payload: dict[str, Any]) -> list[OrderLineView]:
    """Order lines with names and units taken from the database."""
    lines = []
    for raw in payload["lines"]:
        product = session.get(Product, raw["sku"])
        if product is None:
            raise DocumentError(f"Proizvod {raw['sku']} ne postoji.")
        lines.append(
            OrderLineView(
                product.sku,
                product.name_hr,
                product.unit,
                Decimal(str(raw["quantity"])),
                Decimal(str(raw["unit_price_net"])),
            )
        )
    return lines


def _date_hr(value: date) -> str:
    return value.strftime("%d.%m.%Y.")


def order_email(
    supplier: Supplier, lines: list[OrderLineView], delivery: date | None, shop: ShopInfo
) -> tuple[str, str]:
    """Subject and body of the supplier email (Croatian)."""
    total = sum((line.total_net for line in lines), Decimal("0"))
    subject = f"Narudžba – {shop.name}" if shop.name else "Narudžba"
    if delivery:
        subject += f" – isporuka {_date_hr(delivery)}"
    rows = [
        f"- {line.name} ({line.sku}): {fmt_num(line.quantity)} {line.unit} × "
        f"{fmt_money(line.unit_price_net)} = {fmt_money(line.total_net)}"
        for line in lines
    ]
    when = f" za isporuku {_date_hr(delivery)}" if delivery else ""
    body = "\n".join(
        [
            "Poštovani,",
            "",
            f"molimo vas isporuku sljedeće robe{when}:",
            "",
            *rows,
            "",
            f"Ukupno bez PDV-a: {fmt_money(total)}",
            "",
            *([f"Adresa dostave: {shop.address}"] if shop.address else []),
            "Molimo potvrdu narudžbe i termina isporuke odgovorom na ovaj e-mail.",
            "",
            "Lijep pozdrav,",
            shop.name or shop.address or "",
            "",
            "--",
            DISCLOSURE_HR,
            ORDER_NOTE,
        ]
    )
    return subject, body


def order_csv(lines: list[OrderLineView]) -> bytes:
    """Order sheet as CSV (semicolon, decimal comma, UTF-8 with BOM for spreadsheets)."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["sifra", "naziv", "jedinica", "kolicina", "cijena_bez_pdv", "iznos_bez_pdv"])
    for line in lines:
        writer.writerow(
            [
                line.sku,
                line.name,
                line.unit,
                fmt_num(line.quantity),
                f"{line.unit_price_net:.2f}".replace(".", ","),
                f"{line.total_net:.2f}".replace(".", ","),
            ]
        )
    return ("﻿" + out.getvalue()).encode("utf-8")


def order_pdf(
    supplier: Supplier,
    lines: list[OrderLineView],
    delivery: date | None,
    shop: ShopInfo,
    brand: Brand,
    font_path: Path,
    order_no: int,
) -> bytes:
    """A4 order sheet. Fails loudly if the font is missing."""
    if not font_path.is_file():
        raise DocumentError(f"Font za PDF ne postoji: {font_path} (PDF_FONT_PATH)")
    pdf = FPDF(format="A4")
    pdf.add_font("body", "", str(font_path))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    if brand.has("logo_png"):
        pdf.image(str(brand.asset("logo_png")), x=15, y=12, h=14)
        pdf.ln(18)
    pdf.set_font("body", size=16)
    pdf.cell(0, 10, f"Narudžba br. {order_no}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("body", size=10)
    for text in (
        f"Dobavljač: {supplier.name}",
        f"Kupac: {shop.name + ', ' if shop.name else ''}{shop.address or ''}",
        f"Željena isporuka: {_date_hr(delivery)}" if delivery else "",
    ):
        if text:
            pdf.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    widths = (25, 70, 25, 20, 20, 25)
    headers = ("Šifra", "Naziv", "Jedinica", "Količina", "Cijena", "Iznos")
    for w, h in zip(widths, headers, strict=True):
        pdf.cell(w, 7, h, border=1)
    pdf.ln()
    total = Decimal("0")
    for line in lines:
        total += line.total_net
        cells = (
            line.sku,
            line.name,
            line.unit,
            fmt_num(line.quantity),
            fmt_money(line.unit_price_net),
            fmt_money(line.total_net),
        )
        for i, (w, value) in enumerate(zip(widths, cells, strict=True)):
            pdf.cell(w, 7, value[:40], border=1, align="R" if i >= 3 else "L")
        pdf.ln()
    pdf.cell(sum(widths[:-1]), 7, "Ukupno bez PDV-a", border=1, align="R")
    pdf.cell(widths[-1], 7, fmt_money(total), border=1, align="R")
    pdf.ln(14)
    pdf.set_font("body", size=8)
    pdf.multi_cell(0, 5, f"{DISCLOSURE_HR} {ORDER_NOTE}")
    return bytes(pdf.output())


def draft_references(supplier: Supplier, lines: list[OrderLineView]) -> list[dict[str, Any]]:
    """Machine-checkable references for the email draft."""
    refs: list[dict[str, Any]] = [
        {"kind": "supplier", "id": str(supplier.id), "fields": {"name": supplier.name}}
    ]
    for line in lines:
        refs.append(
            {
                "kind": "product",
                "id": line.sku,
                "fields": {
                    "name_hr": line.name,
                    "unit": line.unit,
                    "purchase_price_net": str(line.unit_price_net),
                },
            }
        )
    return refs
