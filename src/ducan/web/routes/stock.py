"""Stock overview, physical count (inventura) and manual adjustments."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.auth.security import require_csrf, require_login
from ducan.inventory.reconciliation import apply_physical_count, check_integrity
from ducan.inventory.stock import record_movement
from ducan.models import MovementKind, Product, StockLevel, StockMovement
from ducan.web.deps import get_session
from ducan.web.routes._forms import FormError, dec

router = APIRouter(prefix="/zalihe")


def _render(request: Request, session: Session, **extra: object) -> Response:
    products = list(session.scalars(select(Product).order_by(Product.category, Product.name_hr)))
    levels = {lvl.sku: lvl for lvl in session.scalars(select(StockLevel))}
    movements = list(
        session.scalars(select(StockMovement).order_by(StockMovement.id.desc()).limit(30))
    )
    context = {
        "products": products,
        "levels": levels,
        "movements": movements,
        "differences": None,
        "errors": [],
        "integrity": None,
        "message": None,
    }
    context.update(extra)
    status_code = 422 if context["errors"] else 200
    return request.app.state.templates.TemplateResponse(
        request, "stock.html", context, status_code=status_code
    )


@router.get("", response_class=HTMLResponse)
def stock_overview(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    return _render(request, session)


@router.post("/inventura", dependencies=[Depends(require_csrf)])
async def physical_count(
    request: Request, actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    form = await request.form()
    counts: dict[str, Decimal] = {}
    try:
        for key, value in form.items():
            if key.startswith("count_") and isinstance(value, str) and value.strip():
                sku = key.removeprefix("count_")
                counts[sku] = dec(value, sku)  # type: ignore[assignment]
    except FormError as exc:
        return _render(request, session, errors=[str(exc)])
    if not counts:
        return _render(request, session, errors=["Nije upisana nijedna prebrojana količina."])
    note = str(form.get("note") or "").strip()
    try:
        differences = apply_physical_count(session, counts, actor, note)
    except ValueError as exc:
        session.rollback()
        return _render(request, session, errors=[str(exc)])
    session.commit()
    return _render(request, session, differences=differences)


@router.post("/korekcija", dependencies=[Depends(require_csrf)])
def manual_adjustment(
    request: Request,
    sku: str = Form(...),
    delta: str = Form(...),
    reason: str = Form(...),
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    errors = []
    try:
        amount = dec(delta, "Promjena")
    except FormError as exc:
        errors.append(str(exc))
        amount = None
    if session.get(Product, sku) is None:
        errors.append(f"Proizvod {sku} ne postoji.")
    if amount in (None, Decimal("0")):
        errors.append("Promjena mora biti broj različit od 0.")
    if not reason.strip():
        errors.append("Razlog je obavezan.")
    if errors:
        return _render(request, session, errors=errors)
    record_movement(
        session,
        sku=sku,
        delta=amount,
        kind=MovementKind.MANUAL_ADJUSTMENT,
        actor=actor,
        reason=reason.strip(),
    )
    record(
        session,
        actor=actor,
        event="stock.adjusted",
        entity_type="product",
        entity_id=sku,
        summary=f"Ručna korekcija {sku}: {amount}",
        details={"reason": reason.strip()},
    )
    session.commit()
    return _render(request, session, message=f"Korekcija za {sku} je zabilježena.")


@router.post("/provjera", dependencies=[Depends(require_csrf)])
def integrity(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    return _render(request, session, integrity=check_integrity(session))
