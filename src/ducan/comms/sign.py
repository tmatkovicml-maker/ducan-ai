"""Printable A4 sign for the shop window: the AI disclosure in four languages."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

from ducan.brand import Brand
from ducan.comms.disclosure import DISCLOSURE

SUB = {
    "hr": "Narudžbe, cijene i raspored priprema AI, a svaku odluku odobrava čovjek.",
    "en": "Orders, prices and rotas are prepared by an AI; a person approves every decision.",
    "de": "Bestellungen, Preise und Dienstpläne bereitet eine KI vor; jede Entscheidung "
    "genehmigt ein Mensch.",
    "it": "Ordini, prezzi e turni sono preparati da un'IA; ogni decisione è approvata da una "
    "persona.",
}


def window_sign(brand: Brand, font_path: Path) -> bytes:
    """A4 portrait PDF. Uses the brand logo only if it is listed in brand.yaml."""
    if not font_path.is_file():
        raise FileNotFoundError(f"Font za PDF ne postoji: {font_path} (PDF_FONT_PATH)")
    pdf = FPDF(format="A4")
    pdf.add_font("body", "", str(font_path))
    bold = font_path.with_name(font_path.stem + "-Bold" + font_path.suffix)
    pdf.add_font("body", "B", str(bold if bold.is_file() else font_path))
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    y = 25
    if brand.has("logo_png"):
        pdf.image(str(brand.asset("logo_png")), x=20, y=y, h=25)
        y += 35
    pdf.set_y(y)
    pdf.set_font("body", "B", 30)
    pdf.multi_cell(0, 14, DISCLOSURE["hr"], align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("body", "", 15)
    pdf.multi_cell(0, 8, SUB["hr"], align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    for lang in ("en", "de", "it"):
        pdf.set_font("body", "B", 17)
        pdf.multi_cell(0, 9, DISCLOSURE[lang], align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("body", "", 12)
        pdf.multi_cell(0, 6.5, SUB[lang], align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(7)
    pdf.set_y(-30)
    pdf.set_font("body", "", 11)
    pdf.multi_cell(
        0,
        6,
        "Za sva pitanja obratite se osoblju. · Please ask our staff. · "
        "Fragen Sie unser Personal. · Chiedete al personale.",
        align="L",
    )
    return bytes(pdf.output())
