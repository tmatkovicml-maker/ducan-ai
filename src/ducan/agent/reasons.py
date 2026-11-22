"""LLM-written justification for price proposals, validated against the database.

The numbers in the proposal are computed by code. The model only phrases the
reason, using read-only tools; its text must pass the reference validator,
otherwise the deterministic template text is used.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.agent.client import Agent, LLMUnavailable, Tool
from ducan.analytics.sales import units_by_sku
from ducan.audit.log import record
from ducan.models import CompetitorPrice, Product
from ducan.validation.references import Draft, Reference, validate_draft

SYSTEM = (
    "You write the short justification the owner of a small shop in Mali Lošinj, Croatia, "
    "reads before approving a proposed retail price change. Write in Croatian, at most two "
    "sentences, plain and factual, no marketing language. Use only facts from the request "
    "and from the tools. Never invent competitors, promotions, deliveries, agreements or "
    "numbers; if a fact is not available, leave it out. Write prices exactly as given, with "
    "a decimal comma and the € sign (for example 1,29 €). When you are done, call the "
    "submit_reason tool with the text."
)


def _tools(session: Session, tz: str, today: date) -> list[Tool]:
    def get_product(args: dict[str, Any]) -> dict[str, Any]:
        product = session.get(Product, args["sku"])
        if product is None:
            raise ValueError("unknown SKU")
        return {
            "sku": product.sku,
            "name": product.name_hr,
            "category": product.category,
            "retail_price_gross_eur": str(product.retail_price_gross),
            "purchase_price_net_eur": str(product.purchase_price_net),
            "vat_rate_pct": str(product.vat_rate),
        }

    def get_competitor_prices(args: dict[str, Any]) -> list[dict[str, str]]:
        rows = session.scalars(
            select(CompetitorPrice)
            .where(CompetitorPrice.sku == args["sku"])
            .order_by(CompetitorPrice.observed_on.desc())
            .limit(10)
        )
        return [
            {
                "competitor": r.competitor,
                "price_gross_eur": str(r.price_gross),
                "observed_on": r.observed_on.isoformat(),
            }
            for r in rows
        ]

    def get_sales(args: dict[str, Any]) -> dict[str, str]:
        units = units_by_sku(session, today - timedelta(days=28), today - timedelta(days=1), tz)
        return {"units_sold_last_28_days": str(units.get(args["sku"], Decimal("0")))}

    sku_schema = {
        "type": "object",
        "properties": {"sku": {"type": "string"}},
        "required": ["sku"],
        "additionalProperties": False,
    }
    return [
        Tool(
            "get_product", "Current product facts from the shop database.", sku_schema, get_product
        ),
        Tool(
            "get_competitor_prices",
            "Competitor prices the owner typed in.",
            sku_schema,
            get_competitor_prices,
        ),
        Tool("get_sales", "Units sold in the last 28 days.", sku_schema, get_sales),
        Tool(
            "submit_reason",
            "Submit the final justification text (Croatian).",
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        ),
    ]


def price_reason(
    session: Session,
    agent: Agent | None,
    *,
    facts: dict[str, Any],
    template: str,
    references: list[Reference],
    allowed_money: list[Decimal],
    tz: str,
    today: date,
) -> tuple[str, str]:
    """Return (reason text, source) where source is "llm" or "template"."""
    if agent is None:
        return template, "template"
    try:
        run = agent.run(
            session,
            purpose=f"price_reason {facts.get('sku')}",
            system=SYSTEM,
            user="Proposed price change (computed by the shop system):\n"
            + "\n".join(f"{k}: {v}" for k, v in facts.items()),
            tools=_tools(session, tz, today),
            submit_tool="submit_reason",
        )
    except LLMUnavailable:
        return template, "template"
    text = (run.submitted or {}).get("text", "").strip()
    if not text:
        return template, "template"
    check = validate_draft(session, Draft(text=text, references=references), allowed_money)
    if not check.ok:
        record(
            session,
            actor="system",
            event="llm.output_rejected",
            summary=f"Obrazloženje za {facts.get('sku')} nije prošlo provjeru",
            details={"text": text, "errors": check.errors},
        )
        return template, "template"
    return text, "llm"
