"""Multilingual social post drafts from the content calendar and real data."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from ducan.actions import service
from ducan.agent.client import Agent, LLMUnavailable, Tool
from ducan.audit.log import record
from ducan.brand import Brand, BrandAssetError
from ducan.comms.checks import check_public_text
from ducan.comms.disclosure import with_disclosure
from ducan.comms.tools import opening_hours_for, price_references, product_facts, read_tools
from ducan.config import BusinessConfig
from ducan.models import Action, ActionType, ContentCalendarEntry, Product
from ducan.validation.references import Reference

TOPICS = {
    "opening_hours": "radno vrijeme",
    "local_products": "lokalni proizvodi",
    "seasonal_essentials": "sezonske potrepštine",
    "product": "pojedini proizvod",
}
LANG_NAMES = {"hr": "Croatian", "en": "English", "de": "German", "it": "Italian"}

SYSTEM = (
    "You write short social media posts for a small convenience and local-products shop on "
    "the waterfront (riva) in Mali Lošinj, Croatia. Use only facts from the request and the "
    "tools. Never invent products, prices, discounts, promotions, events, deliveries or "
    "opening hours, and do not use words like discount, sale, offer or free. Keep product "
    "names in Croatian exactly as the tools give them. Write prices with a decimal comma and "
    "the € sign (1,29 €) and times as HH:MM. Two to four sentences per language, friendly, "
    "no hashtags spam (at most two), at most one emoji. Do not mention AI; the shop adds its "
    "own disclosure line. When done, call submit_post with one text per requested language."
)


class PostNotDrafted(RuntimeError):
    """The post could not be drafted; the message says why (in Croatian)."""


def _assets(brand: Brand, keys: list[str]) -> list[str]:
    for key in keys:
        brand.asset(key)  # raises BrandAssetError if unlisted or missing
    return keys


def draft_post(
    session: Session,
    config: BusinessConfig,
    brand: Brand,
    entry: ContentCalendarEntry,
    agent: Agent | None,
    today: date,
) -> Action:
    """Draft and queue a social_post action. Raises PostNotDrafted with the reason."""
    if agent is None:
        raise PostNotDrafted(
            "AI model nije postavljen (ANTHROPIC_MODEL); objava nije pripremljena."
        )
    try:
        assets = _assets(brand, entry.brand_assets or [])
    except BrandAssetError as exc:
        raise PostNotDrafted(f"Slika iz brand mape nije dostupna: {exc}") from exc
    products = [session.get(Product, sku) for sku in entry.skus]
    missing = [sku for sku, p in zip(entry.skus, products, strict=True) if p is None]
    if missing:
        raise PostNotDrafted(f"Proizvodi ne postoje: {', '.join(missing)}")
    facts: dict[str, Any] = {
        "topic": TOPICS.get(entry.topic, entry.topic),
        "publish_on": entry.publish_on.isoformat(),
        "products": [product_facts(session, p) for p in products if p is not None],
        "opening_hours_next_7_days": opening_hours_for(config, entry.publish_on, 7),
        "owner_notes": entry.notes or "",
    }
    languages = entry.languages or ["hr"]
    submit = Tool(
        "submit_post",
        "Submit the post, one text per language.",
        {
            "type": "object",
            "properties": {lang: {"type": "string"} for lang in languages},
            "required": languages,
            "additionalProperties": False,
        },
    )
    try:
        run = agent.run(
            session,
            purpose=f"social_post calendar #{entry.id}",
            system=SYSTEM,
            user=f"Write the post in: {', '.join(LANG_NAMES[x] for x in languages)}.\n"
            f"Facts: {facts}",
            tools=[*read_tools(session, config, today), submit],
            submit_tool="submit_post",
        )
    except LLMUnavailable as exc:
        raise PostNotDrafted(f"AI model trenutno nije dostupan: {exc}") from exc
    if not run.submitted:
        raise PostNotDrafted("Model nije predao objavu.")

    refs = price_references(session, entry.skus)
    texts: dict[str, str] = {}
    errors: list[str] = []
    for lang in languages:
        text = with_disclosure(str(run.submitted.get(lang, "")).strip(), lang)
        check = check_public_text(session, text, [Reference.from_dict(r) for r in refs], config)
        errors += [f"[{lang}] {e}" for e in check.errors]
        texts[lang] = text
    if errors:
        record(
            session,
            actor="system",
            event="llm.output_rejected",
            summary=f"Objava za kalendar #{entry.id} nije prošla provjeru",
            details={"texts": texts, "errors": errors},
        )
        raise PostNotDrafted("Nacrt nije prošao provjeru: " + "; ".join(errors[:3]))
    action = service.propose(
        session,
        config,
        action_type=ActionType.SOCIAL_POST,
        title=f"{entry.publish_on:%d.%m.}, {TOPICS.get(entry.topic, entry.topic)}",
        payload={
            "calendar_id": entry.id,
            "publish_on": entry.publish_on.isoformat(),
            "texts": texts,
            "brand_assets": assets,
        },
        references=refs,
        reasoning_summary=f"Tema iz kalendara: "
        f"{TOPICS.get(entry.topic, entry.topic)}. Svi podaci iz baze i radnog vremena.",
        data_used=facts,
        proposed_by="agent",
    )
    entry.action_id = action.id
    return action
