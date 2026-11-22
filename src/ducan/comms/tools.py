"""Read-only tools the language model may call. Facts come only from here."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.agent.client import Tool
from ducan.config import WEEKDAYS, BusinessConfig
from ducan.models import FaqEntry, Product, StockLevel

DAY_NAMES = {
    "mon": "ponedjeljak",
    "tue": "utorak",
    "wed": "srijeda",
    "thu": "četvrtak",
    "fri": "petak",
    "sat": "subota",
    "sun": "nedjelja",
}


def _schema(**props: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {k: {"type": v} for k, v in props.items()},
        "required": list(props),
        "additionalProperties": False,
    }


def product_facts(session: Session, product: Product) -> dict[str, Any]:
    level = session.get(StockLevel, product.sku)
    return {
        "sku": product.sku,
        "name_hr": product.name_hr,
        "category": product.category,
        "unit": product.unit,
        "price_eur": None
        if product.retail_price_gross is None
        else str(product.retail_price_gross),
        "in_stock": bool(level and level.quantity > 0),
    }


def opening_hours_for(config: BusinessConfig, start: date, days: int) -> list[dict[str, str]]:
    """Opening hours per day from the configuration ('closed' or 'HH:MM-HH:MM')."""
    out = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        season = config.staffing.season_for(day)
        span = season.hours.get(WEEKDAYS[day.weekday()]) if season else None
        out.append(
            {
                "date": day.isoformat(),
                "weekday": DAY_NAMES[WEEKDAYS[day.weekday()]],
                "hours": span or ("zatvoreno" if season else "nepoznato"),
            }
        )
    return out


def faq_search(session: Session, query: str, language: str, limit: int = 3) -> list[dict]:
    """Reviewed FAQ entries ranked by shared words with the query."""
    words = {w for w in _words(query) if len(w) > 2}
    scored = []
    for entry in session.scalars(select(FaqEntry).where(FaqEntry.reviewed.is_(True))):
        haystack = set(
            _words(" ".join([entry.question_hr, entry.keywords, *entry.answers.values()]))
        )
        score = len(words & haystack)
        if score:
            answer = entry.answers.get(language) or entry.answers.get("hr", "")
            scored.append(
                (score, {"id": entry.id, "question": entry.question_hr, "answer": answer})
            )
    scored.sort(key=lambda item: -item[0])
    return [item for _score, item in scored[:limit]]


def _words(text: str) -> list[str]:
    import re

    return re.findall(r"[a-zčćđšžäöüßàèéìòù0-9]+", text.casefold())


def read_tools(session: Session, config: BusinessConfig, today: date) -> list[Tool]:
    """The read-only tools shared by posts, review replies and the chat."""

    def get_product(args: dict[str, Any]) -> dict[str, Any]:
        product = session.get(Product, args["sku"])
        if product is None or not product.active:
            raise ValueError("unknown or inactive SKU")
        return product_facts(session, product)

    def find_products(args: dict[str, Any]) -> list[dict[str, Any]]:
        words = [w for w in _words(args["query"]) if len(w) > 2]
        rows = session.scalars(select(Product).where(Product.active.is_(True)))
        hits = [
            p
            for p in rows
            if any(w in p.name_hr.casefold() or w in p.category.casefold() for w in words)
        ]
        return [product_facts(session, p) for p in hits[:8]]

    def get_opening_hours(args: dict[str, Any]) -> list[dict[str, str]]:
        days = max(1, min(int(args["days"]), 14))
        return opening_hours_for(config, today, days)

    def search_faq(args: dict[str, Any]) -> list[dict]:
        return faq_search(session, args["query"], args["language"])

    return [
        Tool(
            "get_product",
            "Facts about one active product by SKU (name, price, in stock).",
            _schema(sku="string"),
            get_product,
        ),
        Tool(
            "find_products",
            "Search active products by name or category words (Croatian).",
            _schema(query="string"),
            find_products,
        ),
        Tool(
            "get_opening_hours",
            "Opening hours for the next N days (1-14) from today.",
            _schema(days="integer"),
            get_opening_hours,
        ),
        Tool(
            "search_faq",
            "Search the owner-reviewed FAQ. language: hr, en, de or it.",
            _schema(query="string", language="string"),
            search_faq,
        ),
    ]


def price_references(session: Session, skus: list[str]) -> list[dict[str, Any]]:
    """References that allow the text to mention these products and their prices."""
    refs = []
    for sku in skus:
        product = session.get(Product, sku)
        if product is None:
            continue
        fields: dict[str, Any] = {"name_hr": product.name_hr}
        if product.retail_price_gross is not None:
            fields["retail_price_gross"] = str(product.retail_price_gross)
        refs.append({"kind": "product", "id": sku, "fields": fields})
    return refs
