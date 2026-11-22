"""Approval queue UI: approve, reject, approve all low-risk, roll back."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.actions import service
from ducan.actions.executors import NotReversible
from ducan.actions.executors.price_change import StaleProposal
from ducan.actions.state_machine import InvalidTransition
from ducan.auth.security import require_csrf, require_login
from ducan.config import AppConfig
from ducan.inventory.receiving import ReceivingError, receive_order
from ducan.models import Action, ActionStatus, ActionType, Supplier
from ducan.replenishment.documents import DocumentError, load_lines, order_csv, order_pdf
from ducan.web.deps import get_config, get_session
from ducan.web.routes._forms import FormError, dec

router = APIRouter(prefix="/akcije")
FILTERS = {
    "na-cekanju": [ActionStatus.PROPOSED],
    "odobreno": [ActionStatus.APPROVED],
    "izvrseno": [ActionStatus.EXECUTED],
    "zavrseno": [ActionStatus.REJECTED, ActionStatus.FAILED, ActionStatus.ROLLED_BACK],
}


def _card(request: Request, action: Action, error: str | None = None) -> Response:
    return request.app.state.templates.TemplateResponse(
        request, "_action_card.html", {"action": action, "error": error}
    )


def _respond(request: Request, action: Action, error: str | None = None) -> Response:
    if request.headers.get("hx-request"):
        return _card(request, action, error)
    return RedirectResponse("/akcije", status_code=303)


@router.get("", response_class=HTMLResponse)
def list_actions(
    request: Request,
    status: str = "na-cekanju",
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    statuses = FILTERS.get(status, FILTERS["na-cekanju"])
    actions = list(
        session.scalars(
            select(Action).where(Action.status.in_(statuses)).order_by(Action.id.desc()).limit(100)
        )
    )
    low_risk = sum(1 for a in actions if a.status == ActionStatus.PROPOSED and a.risk == "low")
    return request.app.state.templates.TemplateResponse(
        request,
        "actions.html",
        {"actions": actions, "filter": status, "filters": FILTERS, "low_risk": low_risk},
    )


def _load(session: Session, action_id: int) -> Action:
    action = session.get(Action, action_id)
    if action is None:
        raise HTTPException(404, "Akcija ne postoji.")
    return action


@router.post("/{action_id}/odobri", dependencies=[Depends(require_csrf)])
def approve(
    action_id: int,
    request: Request,
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    _load(session, action_id)
    try:
        action = service.approve(session, config.business, action_id, actor)
    except InvalidTransition as exc:
        session.rollback()
        return _respond(request, _load(session, action_id), str(exc))
    session.commit()
    return _respond(request, action)


@router.post("/{action_id}/odbij", dependencies=[Depends(require_csrf)])
def reject(
    action_id: int,
    request: Request,
    reason: str = Form(""),
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    _load(session, action_id)
    try:
        action = service.reject(session, action_id, actor, reason)
    except InvalidTransition as exc:
        session.rollback()
        return _respond(request, _load(session, action_id), str(exc))
    session.commit()
    return _respond(request, action)


@router.post("/{action_id}/ponisti", dependencies=[Depends(require_csrf)])
def roll_back(
    action_id: int,
    request: Request,
    reason: str = Form(...),
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    _load(session, action_id)
    if not reason.strip():
        return _respond(request, _load(session, action_id), "Razlog poništenja je obavezan.")
    try:
        action = service.rollback(session, action_id, actor, reason.strip())
    except NotReversible as exc:
        session.commit()  # keep the audit entry for the refused rollback
        return _respond(request, _load(session, action_id), str(exc))
    except (InvalidTransition, StaleProposal) as exc:
        session.rollback()
        return _respond(request, _load(session, action_id), str(exc))
    session.commit()
    return _respond(request, action)


def _order(session: Session, action_id: int) -> Action:
    action = _load(session, action_id)
    if action.type != ActionType.PURCHASE_ORDER:
        raise HTTPException(404, "Akcija nije narudžba.")
    return action


@router.get("/{action_id}/narudzba.pdf")
def order_pdf_download(
    action_id: int,
    request: Request,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    action = _order(session, action_id)
    supplier = session.get(Supplier, action.payload["supplier_id"])
    delivery = action.payload.get("delivery_date")
    try:
        pdf = order_pdf(
            supplier,
            load_lines(session, action.payload),
            date.fromisoformat(delivery) if delivery else None,
            config.business.shop,
            request.app.state.brand,
            config.env.pdf_font_path,
            action.id,
        )
    except DocumentError as exc:
        raise HTTPException(500, str(exc)) from exc
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="narudzba-{action.id}.pdf"'},
    )


@router.get("/{action_id}/narudzba.csv")
def order_csv_download(
    action_id: int,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    action = _order(session, action_id)
    return Response(
        order_csv(load_lines(session, action.payload)),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="narudzba-{action.id}.csv"'},
    )


@router.post("/{action_id}/primka", dependencies=[Depends(require_csrf)])
async def receive(
    action_id: int,
    request: Request,
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    action = _order(session, action_id)
    form = await request.form()
    received = {}
    try:
        for key, value in form.items():
            if key.startswith("qty_") and isinstance(value, str) and value.strip():
                received[key.removeprefix("qty_")] = dec(value, key.removeprefix("qty_"))
        receive_order(session, action, received, actor)
    except (FormError, ReceivingError) as exc:
        session.rollback()
        return _respond(request, _load(session, action_id), str(exc))
    session.commit()
    return _respond(request, action)


@router.post("/odobri-niski-rizik", dependencies=[Depends(require_csrf)])
def approve_low_risk(
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    service.approve_all_low_risk(session, config.business, actor)
    session.commit()
    return RedirectResponse("/akcije", status_code=303)
