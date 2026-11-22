"""Supplier CRUD. Unknown terms stay empty and are shown as "nepoznato"."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.auth.security import require_csrf, require_login
from ducan.models import Supplier
from ducan.web.deps import get_session
from ducan.web.routes._forms import FormError, dec, integer, text

router = APIRouter(prefix="/dobavljaci")
DELIVERY_DAYS_RE = re.compile(r"^[1-7](,[1-7])*$")


def _parse(form: Any) -> tuple[dict[str, Any], list[str]]:
    try:
        data = {
            "name": text(form.get("name")),
            "contact_name": text(form.get("contact_name")),
            "email": text(form.get("email")),
            "phone": text(form.get("phone")),
            "lead_time_days": integer(form.get("lead_time_days"), "Rok isporuke"),
            "min_order_net": dec(form.get("min_order_net"), "Minimalna narudžba"),
            "delivery_days": text((form.get("delivery_days") or "").replace(" ", "")),
            "payment_terms": text(form.get("payment_terms")),
            "notes": text(form.get("notes")),
            "active": form.get("active") == "on",
        }
    except FormError as exc:
        return {}, [str(exc)]
    errors = []
    if not data["name"]:
        errors.append("Naziv je obavezan.")
    if data["delivery_days"] and not DELIVERY_DAYS_RE.match(data["delivery_days"]):
        errors.append("Dani dostave: brojevi 1–7 odvojeni zarezom (1 = ponedjeljak).")
    if data["email"] and "@" not in data["email"]:
        errors.append("E-mail adresa nije ispravna.")
    return data, errors


def _render(
    request: Request,
    supplier: Supplier | None,
    values: dict[str, Any],
    errors: list[str],
    status_code: int = 200,
) -> Response:
    return request.app.state.templates.TemplateResponse(
        request,
        "supplier_form.html",
        {"supplier": supplier, "values": values, "errors": errors},
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def list_suppliers(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    suppliers = list(session.scalars(select(Supplier).order_by(Supplier.name)))
    return request.app.state.templates.TemplateResponse(
        request, "suppliers.html", {"suppliers": suppliers}
    )


@router.get("/novi", response_class=HTMLResponse)
def new_supplier_form(request: Request, _actor: str = Depends(require_login)) -> Response:
    return _render(request, None, {"active": True}, [])


@router.post("/novi", dependencies=[Depends(require_csrf)])
async def create_supplier(
    request: Request, actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    data, errors = _parse(await request.form())
    if not errors and session.scalar(select(Supplier).where(Supplier.name == data["name"])):
        errors.append("Dobavljač s tim nazivom već postoji.")
    if errors:
        return _render(request, None, data, errors, 422)
    supplier = Supplier(**data)
    session.add(supplier)
    session.flush()
    record(
        session,
        actor=actor,
        event="supplier.created",
        entity_type="supplier",
        entity_id=supplier.id,
        summary=f"Novi dobavljač {supplier.name}",
    )
    session.commit()
    return RedirectResponse("/dobavljaci", status_code=303)


@router.get("/{supplier_id}", response_class=HTMLResponse)
def edit_supplier_form(
    supplier_id: int,
    request: Request,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(404, "Dobavljač ne postoji.")
    values = {c.key: getattr(supplier, c.key) for c in Supplier.__table__.columns}
    return _render(request, supplier, values, [])


@router.post("/{supplier_id}", dependencies=[Depends(require_csrf)])
async def update_supplier(
    supplier_id: int,
    request: Request,
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(404, "Dobavljač ne postoji.")
    data, errors = _parse(await request.form())
    if errors:
        return _render(request, supplier, data, errors, 422)
    changes = {}
    for key, value in data.items():
        old = getattr(supplier, key)
        if old != value:
            changes[key] = {
                "old": None if old is None else str(old),
                "new": None if value is None else str(value),
            }
            setattr(supplier, key, value)
    if changes:
        record(
            session,
            actor=actor,
            event="supplier.updated",
            entity_type="supplier",
            entity_id=supplier.id,
            summary=f"Izmjena dobavljača {supplier.name}",
            details=changes,
        )
    session.commit()
    return RedirectResponse("/dobavljaci", status_code=303)
