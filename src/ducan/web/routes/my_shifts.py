"""Personal page for staff (link with a secret token from the rota email; no login)."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.auth.security import require_csrf
from ducan.models import Staff, SwapRequest
from ducan.scheduling.service import published_shifts, staff_by_token
from ducan.scheduling.swaps import SwapError, request_swap
from ducan.web.deps import get_session

router = APIRouter(prefix="/smjene")


def _render(
    request: Request,
    session: Session,
    staff: Staff,
    token: str,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    today = date.today()
    shifts = published_shifts(session, today, today + timedelta(days=21), staff.id)
    colleagues = list(
        session.scalars(select(Staff).where(Staff.active.is_(True), Staff.id != staff.id))
    )
    pending = list(
        session.scalars(
            select(SwapRequest).where(
                SwapRequest.from_staff_id == staff.id, SwapRequest.status == "requested"
            )
        )
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "my_shifts.html",
        {
            "staff": staff,
            "shifts": shifts,
            "colleagues": colleagues,
            "token": token,
            "pending": pending,
            "message": message,
            "error": error,
        },
        status_code=422 if error else 200,
    )


def _staff(session: Session, token: str) -> Staff:
    staff = staff_by_token(session, token)
    if staff is None:
        raise HTTPException(404, "Poveznica nije valjana. Zatraži novu od vlasnika.")
    return staff


@router.get("/{token}", response_class=HTMLResponse)
def my_shifts(token: str, request: Request, session: Session = Depends(get_session)) -> Response:
    return _render(request, session, _staff(session, token), token)


@router.post("/{token}/zamjena", dependencies=[Depends(require_csrf)])
def ask_swap(
    token: str,
    request: Request,
    shift_id: int = Form(...),
    to_staff_id: int = Form(...),
    reason: str = Form(""),
    session: Session = Depends(get_session),
) -> Response:
    staff = _staff(session, token)
    try:
        request_swap(session, staff, shift_id, to_staff_id, reason)
    except SwapError as exc:
        session.rollback()
        return _render(request, session, staff, token, error=str(exc))
    session.commit()
    return _render(
        request,
        session,
        staff,
        token,
        message="Zahtjev je poslan vlasniku. Dobit ćeš e-mail s odlukom.",
    )
