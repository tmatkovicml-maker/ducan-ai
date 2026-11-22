"""Rota overview, proposing next week's rota now, and deciding shift swaps."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.auth.security import require_csrf, require_login
from ducan.comms.notify import make_notifier
from ducan.config import AppConfig
from ducan.models import Staff, SwapRequest
from ducan.scheduling.job import run_rota
from ducan.scheduling.service import next_week_start, published_shifts, rota_for_week
from ducan.scheduling.swaps import SwapError, decide_swap, swap_problems
from ducan.web.deps import get_config, get_session

router = APIRouter(prefix="/raspored")
DAY_NAMES = ("Pon", "Uto", "Sri", "Čet", "Pet", "Sub", "Ned")


def _today(config: AppConfig) -> date:
    return datetime.now(ZoneInfo(config.business.timezone)).date()


def _render(
    request: Request,
    session: Session,
    config: AppConfig,
    week: date,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    shifts = published_shifts(session, week, week + timedelta(days=7))
    staff = {s.id: s for s in session.scalars(select(Staff))}
    days = [
        (
            week + timedelta(days=i),
            DAY_NAMES[i],
            [s for s in shifts if s.day == week + timedelta(days=i)],
        )
        for i in range(7)
    ]
    swaps = list(
        session.scalars(
            select(SwapRequest).where(SwapRequest.status == "requested").order_by(SwapRequest.id)
        )
    )
    from ducan.models import Shift

    swap_rows = [
        (sw, session.get(Shift, sw.shift_id), swap_problems(session, sw, config.business))
        for sw in swaps
    ]
    nxt = next_week_start(_today(config))
    return request.app.state.templates.TemplateResponse(
        request,
        "rota.html",
        {
            "week": week,
            "days": days,
            "staff": staff,
            "swap_rows": swap_rows,
            "prev": week - timedelta(days=7),
            "next": week + timedelta(days=7),
            "next_week": nxt,
            "next_rota": rota_for_week(session, nxt),
            "missing_rules": config.business.labour.missing(),
            "no_seasons": not config.business.staffing.seasons,
            "message": message,
            "error": error,
        },
        status_code=422 if error else 200,
    )


@router.get("", response_class=HTMLResponse)
def rota_overview(
    request: Request,
    tjedan: str | None = None,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    today = _today(config)
    try:
        week = date.fromisoformat(tjedan) if tjedan else today
    except ValueError:
        week = today
    week -= timedelta(days=week.weekday())
    return _render(request, session, config, week)


@router.post("/predlozi", dependencies=[Depends(require_csrf)])
def propose_now(
    request: Request,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    run = run_rota(session, config, _today(config))
    session.commit()
    if run.summary["proposed"]:
        return RedirectResponse("/akcije", status_code=303)
    week = next_week_start(_today(config))
    return _render(
        request,
        session,
        config,
        week,
        error=" ".join(run.summary["flags"]) or "Raspored nije predložen.",
    )


@router.post("/zamjena/{swap_id}", dependencies=[Depends(require_csrf)])
def decide(
    swap_id: int,
    request: Request,
    odluka: str = Form(...),
    napomena: str = Form(""),
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    swap = session.get(SwapRequest, swap_id)
    if swap is None:
        raise HTTPException(404, "Zahtjev ne postoji.")
    week = _today(config) - timedelta(days=_today(config).weekday())
    try:
        decide_swap(
            session,
            swap,
            odluka == "odobri",
            actor,
            config.business,
            make_notifier(config.env),
            napomena,
        )
    except SwapError as exc:
        session.rollback()
        return _render(request, session, config, week, error=str(exc))
    session.commit()
    return _render(request, session, config, week, message="Odluka je spremljena.")
