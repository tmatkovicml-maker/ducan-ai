"""Competitor prices, entered manually by the owner."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.auth.security import require_csrf, require_login
from ducan.models import CompetitorPrice, Product
from ducan.web.deps import get_session
from ducan.web.routes._forms import FormError, dec

router = APIRouter(prefix="/konkurencija")


def _render(request: Request, session: Session, errors: list[str] | None = None) -> Response:
    rows = list(
        session.scalars(
            select(CompetitorPrice).order_by(CompetitorPrice.observed_on.desc()).limit(200)
        )
    )
    products = list(session.scalars(select(Product).order_by(Product.name_hr)))
    return request.app.state.templates.TemplateResponse(
        request,
        "competitors.html",
        {
            "rows": rows,
            "products": {p.sku: p for p in products},
            "errors": errors or [],
            "today": date.today().isoformat(),
        },
        status_code=422 if errors else 200,
    )


@router.get("", response_class=HTMLResponse)
def list_prices(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    return _render(request, session)


@router.post("", dependencies=[Depends(require_csrf)])
def add_price(
    request: Request,
    sku: str = Form(...),
    competitor: str = Form(...),
    price_gross: str = Form(...),
    observed_on: str = Form(...),
    note: str = Form(""),
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    errors = []
    try:
        price = dec(price_gross, "Cijena")
    except FormError as exc:
        errors.append(str(exc))
        price = None
    if price is None or price <= 0:
        errors.append("Cijena mora biti veća od 0.")
    if session.get(Product, sku) is None:
        errors.append("Proizvod ne postoji.")
    if not competitor.strip():
        errors.append("Upiši naziv konkurenta.")
    try:
        seen = date.fromisoformat(observed_on)
        if seen > date.today():
            errors.append("Datum ne može biti u budućnosti.")
    except ValueError:
        errors.append("Datum nije ispravan.")
        seen = None
    if errors:
        return _render(request, session, errors)
    row = CompetitorPrice(
        sku=sku,
        competitor=competitor.strip(),
        price_gross=price,
        observed_on=seen,
        note=note.strip() or None,
        entered_by=actor,
    )
    session.add(row)
    session.flush()
    record(
        session,
        actor=actor,
        event="competitor_price.added",
        entity_type="product",
        entity_id=sku,
        summary=f"Cijena konkurencije {competitor.strip()}: {price} €",
        details={"observed_on": observed_on},
    )
    session.commit()
    return RedirectResponse("/konkurencija", status_code=303)


@router.post("/{row_id}/obrisi", dependencies=[Depends(require_csrf)])
def delete_price(
    row_id: int, actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    row = session.get(CompetitorPrice, row_id)
    if row is not None:
        record(
            session,
            actor=actor,
            event="competitor_price.deleted",
            entity_type="product",
            entity_id=row.sku,
            summary=f"Obrisana cijena konkurencije {row.competitor}",
            details={
                "price_gross": str(row.price_gross),
                "observed_on": row.observed_on.isoformat(),
            },
        )
        session.delete(row)
        session.commit()
    return RedirectResponse("/konkurencija", status_code=303)
