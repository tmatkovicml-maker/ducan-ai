"""Weekly pricing job: propose price changes with an evidence-based reason.

Two rules, both bounded by the floor margin and rounded to price points:

1. Margin below the floor (e.g. after a purchase price increase): raise to the
   lowest price point that restores the floor.
2. Owner-entered competitor price is lower than ours by more than
   ``competitor_gap_pct``: lower towards it, never below the floor.

Competitor prices come only from manual entry; nothing is scraped.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.actions import service
from ducan.agent.client import Agent, LLMUnavailable
from ducan.agent.reasons import price_reason
from ducan.audit.log import record
from ducan.config import AppConfig
from ducan.db import utcnow
from ducan.formatting import fmt_money
from ducan.models import Action, ActionStatus, ActionType, CompetitorPrice, JobRun, Product
from ducan.pricing.rules import margin_pct, min_gross_for_margin, round_down, round_up
from ducan.validation.references import Reference

JOB_NAME = "pricing"


def _pct(value: Decimal) -> str:
    return f"{value:.1f}".replace(".", ",") + " %"


def _pending_skus(session: Session) -> set[str]:
    stmt = select(Action).where(
        Action.type == ActionType.PRICE_CHANGE, Action.status == ActionStatus.PROPOSED
    )
    return {a.payload.get("sku") for a in session.scalars(stmt)}


def _cheapest_competitor(session: Session, sku: str, since: date) -> CompetitorPrice | None:
    rows = session.scalars(
        select(CompetitorPrice).where(
            CompetitorPrice.sku == sku, CompetitorPrice.observed_on >= since
        )
    )
    return min(rows, key=lambda r: r.price_gross, default=None)


def make_agent(config: AppConfig) -> Agent | None:
    """The LLM agent, or None when no model is configured."""
    try:
        return Agent(config.env)
    except LLMUnavailable:
        return None


def run_pricing(
    session: Session,
    config: AppConfig,
    today: date,
    now: datetime | None = None,
    agent: Agent | None = None,
) -> JobRun:
    """Run the job once. Proposals go to the approval queue."""
    now = now or utcnow()
    rules = config.business.pricing
    tiers = rules.price_points
    run = JobRun(job=JOB_NAME, started_at=now)
    session.add(run)
    session.flush()
    flags: list[str] = []
    proposed: list[int] = []
    pending = _pending_skus(session)
    since = today - timedelta(days=rules.competitor_max_age_days)

    products = session.scalars(
        select(Product).where(Product.active.is_(True)).order_by(Product.sku)
    )
    for product in products:
        floor = rules.floor_for(product.category)
        if floor is None:
            flags.append(
                f"{product.sku}: minimalna marža za kategoriju "
                f"'{product.category}' nije postavljena."
            )
            continue
        if product.sku in pending:
            continue
        current = product.retail_price_gross
        purchase, vat = product.purchase_price_net, product.vat_rate
        if current is None or purchase is None or vat is None:
            continue
        margin = margin_pct(current, vat, purchase)
        floor_price = round_up(min_gross_for_margin(purchase, vat, floor), tiers)
        proposal = _evaluate(
            session,
            product,
            current,
            margin,
            floor,
            floor_price,
            since,
            tiers,
            rules.competitor_gap_pct,
        )
        if proposal is None:
            continue
        new, rule, template, evidence, facts = proposal
        refs = [
            Reference(
                kind="product",
                id=product.sku,
                fields={"retail_price_gross": str(current), "purchase_price_net": str(purchase)},
            )
        ]
        allowed = [current, new, *evidence]
        reason, source = price_reason(
            session,
            agent,
            facts=facts,
            template=template,
            references=refs,
            allowed_money=allowed,
            tz=config.business.timezone,
            today=today,
        )
        try:
            action = service.propose(
                session,
                config.business,
                action_type=ActionType.PRICE_CHANGE,
                title=f"{product.name_hr}: {fmt_money(current)} → {fmt_money(new)}",
                payload={
                    "sku": product.sku,
                    "old_retail_price_gross": str(current),
                    "new_retail_price_gross": str(new),
                    "rule": rule,
                    "evidence_amounts": [str(e) for e in evidence],
                },
                references=[{"kind": r.kind, "id": r.id, "fields": r.fields} for r in refs],
                reasoning_summary=reason,
                data_used={**{k: str(v) for k, v in facts.items()}, "reason_source": source},
                proposed_by="agent",
                now=now,
            )
        except service.ProposalRejected as exc:
            flags.append(f"{product.sku}: prijedlog odbijen u provjeri: {exc}")
            continue
        proposed.append(action.id)

    if (
        not proposed
        and rules.default_floor_margin_pct is None
        and not rules.category_floor_margin_pct
    ):
        flags = [
            "Minimalna marža nije postavljena u config/settings.yaml; "
            "promjene cijena se ne predlažu."
        ]
    run.finished_at = utcnow()
    run.status = "ok"
    run.summary = {"proposed": proposed, "flags": flags, "today": today.isoformat()}
    record(
        session,
        actor="agent",
        event="job.pricing",
        summary=f"Tjedne cijene: {len(proposed)} prijedloga, {len(flags)} napomena",
        details=run.summary,
    )
    return run


def _evaluate(
    session: Session,
    product: Product,
    current: Decimal,
    margin: Decimal,
    floor: Decimal,
    floor_price: Decimal,
    since: date,
    tiers: list,
    gap_pct: Decimal,
) -> tuple[Decimal, str, str, list[Decimal], dict[str, Any]] | None:
    purchase, vat = product.purchase_price_net, product.vat_rate
    assert purchase is not None and vat is not None
    base = {
        "sku": product.sku,
        "product": product.name_hr,
        "current_price_gross_eur": current,
        "purchase_price_net_eur": purchase,
        "vat_rate_pct": vat,
        "current_margin_pct": margin.quantize(Decimal("0.1")),
        "floor_margin_pct": floor,
    }
    if margin < floor:
        new = floor_price
        if new <= current:
            return None
        new_margin = margin_pct(new, vat, purchase)
        template = (
            f"Marža je {_pct(margin)}, ispod minimalne {_pct(floor)} uz nabavnu cijenu "
            f"{fmt_money(purchase)} bez PDV-a. Nova cijena {fmt_money(new)} vraća maržu "
            f"na {_pct(new_margin)}."
        )
        return (
            new,
            "floor_margin",
            template,
            [],
            {
                **base,
                "rule": "margin below floor",
                "new_price_gross_eur": new,
                "new_margin_pct": new_margin.quantize(Decimal("0.1")),
            },
        )

    cheapest = _cheapest_competitor(session, product.sku, since)
    if cheapest is None:
        return None
    gap = Decimal("1") + gap_pct / 100
    if current <= cheapest.price_gross * gap:
        return None
    target = round_down(cheapest.price_gross * gap, tiers)
    new = max(target or floor_price, floor_price)
    if new >= current:
        return None
    new_margin = margin_pct(new, vat, purchase)
    template = (
        f"Najniža cijena kod konkurencije ({cheapest.competitor}, "
        f"{cheapest.observed_on.strftime('%d.%m.%Y.')}) je "
        f"{fmt_money(cheapest.price_gross)}, a naša {fmt_money(current)}. Nova cijena "
        f"{fmt_money(new)} zadržava maržu {_pct(new_margin)} (minimum {_pct(floor)})."
    )
    return (
        new,
        "competitor",
        template,
        [cheapest.price_gross],
        {
            **base,
            "rule": "competitor cheaper",
            "competitor": cheapest.competitor,
            "competitor_price_gross_eur": cheapest.price_gross,
            "competitor_observed_on": cheapest.observed_on.isoformat(),
            "new_price_gross_eur": new,
            "new_margin_pct": new_margin.quantize(Decimal("0.1")),
        },
    )
