"""Nightly replenishment job: forecast, plan per supplier, propose purchase orders."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.actions import service
from ducan.audit.log import record
from ducan.config import AppConfig
from ducan.db import utcnow
from ducan.formatting import fmt_money, fmt_num
from ducan.models import ActionType, JobRun, Product, Supplier
from ducan.replenishment.documents import OrderLineView, draft_references, order_email
from ducan.replenishment.forecast import forecast_products
from ducan.replenishment.planner import SupplierPlan, apply_budget, pending_order, plan_supplier

JOB_NAME = "replenishment"


def _line_view(line: Any) -> OrderLineView:
    return OrderLineView(line.sku, line.name, line.unit, line.quantity, line.unit_price_net)


def _dec(value: Decimal, places: int) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def _reasoning(plan: SupplierPlan) -> str:
    parts = [
        f"{line.sku}: zaliha {fmt_num(line.stock)}, naručeno {fmt_num(line.on_order)}, "
        f"prodaja ~{_dec(line.per_day, 2)}/dan, točka narudžbe {_dec(line.reorder_point, 1)}"
        for line in plan.lines
    ]
    when = plan.delivery_date.strftime("%d.%m.%Y.") if plan.delivery_date else "nepoznato"
    return (
        f"Isporuka {when}; zaliha mora trajati {plan.horizon_days} dana do sljedeće "
        f"moguće isporuke. " + "; ".join(parts)
    )


def run_replenishment(
    session: Session, config: AppConfig, today: date, now: datetime | None = None
) -> JobRun:
    """Run the job once. Proposals go to the approval queue; nothing is sent."""
    now = now or utcnow()
    business = config.business
    run = JobRun(job=JOB_NAME, started_at=now)
    session.add(run)
    session.flush()
    products = list(session.scalars(select(Product).where(Product.active.is_(True))))
    forecasts = forecast_products(session, products, business, today)
    flags: list[str] = []
    proposed: list[int] = []
    to_order: list[dict[str, Any]] = []

    no_supplier = [p for p in products if p.supplier_id is None]
    if no_supplier:
        flags.append("Aktivni proizvodi bez dobavljača: " + ", ".join(p.sku for p in no_supplier))

    suppliers = session.scalars(
        select(Supplier).where(Supplier.active.is_(True)).order_by(Supplier.id)
    )
    for supplier in suppliers:
        plan = plan_supplier(session, supplier, forecasts, business, today)
        if plan.proposable and plan.lines:
            apply_budget(plan, session, business, now)
        flags.extend(plan.flags)
        if not plan.lines:
            continue
        to_order.append(
            {
                "supplier": supplier.name,
                "delivery_date": plan.delivery_date.isoformat() if plan.delivery_date else None,
                "lines": [
                    {
                        "sku": ln.sku,
                        "name": ln.name,
                        "quantity": str(ln.quantity),
                        "total_net": str(ln.total_net),
                    }
                    for ln in plan.lines
                ],
                "total_net": str(plan.total_net),
                "proposable": plan.proposable,
            }
        )
        if not plan.proposable:
            continue
        waiting = pending_order(session, supplier.id)
        if waiting is not None:
            flags.append(f"{supplier.name}: prijedlog #{waiting.id} još čeka odobrenje.")
            continue
        if not supplier.email:
            flags.append(f"{supplier.name}: nema e-mail adrese; narudžbu treba poslati ručno.")

        views = [_line_view(line) for line in plan.lines]
        subject, body = order_email(supplier, views, plan.delivery_date, business.shop)
        payload = {
            "supplier_id": supplier.id,
            "delivery_date": plan.delivery_date.isoformat() if plan.delivery_date else None,
            "lines": [
                {"sku": v.sku, "quantity": str(v.quantity), "unit_price_net": str(v.unit_price_net)}
                for v in views
            ],
            "subject": subject,
            "draft_text": body,
        }
        try:
            action = service.propose(
                session,
                business,
                action_type=ActionType.PURCHASE_ORDER,
                title=f"{supplier.name}: {fmt_money(plan.total_net)} bez PDV-a",
                payload=payload,
                references=draft_references(supplier, views),
                reasoning_summary=_reasoning(plan),
                data_used={
                    "forecasts": {ln.sku: forecasts[ln.sku].as_dict() for ln in plan.lines},
                    "today": today.isoformat(),
                },
                proposed_by="agent",
                now=now,
            )
        except service.ProposalRejected as exc:
            flags.append(f"{supplier.name}: prijedlog odbijen u provjeri: {exc}")
            continue
        proposed.append(action.id)

    run.finished_at = utcnow()
    run.status = "ok"
    run.summary = {
        "proposed": proposed,
        "flags": flags,
        "to_order": to_order,
        "today": today.isoformat(),
        "forecast_unknown": sorted(s for s, f in forecasts.items() if f.per_day is None),
    }
    record(
        session,
        actor="agent",
        event="job.replenishment",
        summary=f"Noćna nabava: {len(proposed)} prijedloga, {len(flags)} napomena",
        details=run.summary,
    )
    return run
