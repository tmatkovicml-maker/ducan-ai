"""Dashboard: sales, margin, stock cover, expiring stock and what to order today."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ducan.analytics.dashboard import (
    expiring,
    latest_run,
    sales_by_category,
    sales_by_day,
    stock_cover,
)
from ducan.auth.security import require_login
from ducan.config import AppConfig
from ducan.models import Action, ActionStatus, ActionType, Product, SalesImport
from ducan.web.deps import get_config, get_session

router = APIRouter()
WINDOW_DAYS = 28


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    business = config.business
    today = datetime.now(ZoneInfo(business.timezone)).date()
    start = today - timedelta(days=WINDOW_DAYS - 1)
    days = sales_by_day(session, start, today, business.timezone)
    peak = max((d.gross for d in days), default=Decimal("0"))
    categories = sales_by_category(session, start, today, business.timezone)
    pending = session.scalar(
        select(func.count()).select_from(Action).where(Action.status == ActionStatus.PROPOSED)
    )
    pending_orders = list(
        session.scalars(
            select(Action)
            .where(Action.type == ActionType.PURCHASE_ORDER, Action.status == ActionStatus.PROPOSED)
            .order_by(Action.id)
        )
    )
    inactive = session.scalar(
        select(func.count()).select_from(Product).where(Product.active.is_(False))
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "pending": pending,
            "pending_orders": pending_orders,
            "days": days,
            "peak": peak,
            "total_gross": sum((d.gross for d in days), Decimal("0")),
            "categories": categories,
            "cover": stock_cover(session, business, today)[:15],
            "expiring": expiring(
                session, business.timezone, today, business.dashboard.expiry_warning_days
            ),
            "replenishment": latest_run(session, "replenishment"),
            "pricing": latest_run(session, "pricing"),
            "inactive_count": inactive,
            "last_import": session.scalar(
                select(SalesImport).order_by(SalesImport.id.desc()).limit(1)
            ),
            "window_days": WINDOW_DAYS,
        },
    )
