"""Start page. The full sales/margin dashboard arrives in Phase 2."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ducan.auth.security import require_login
from ducan.models import Action, ActionStatus, Product, SalesImport, StockLevel
from ducan.web.deps import get_session

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    pending = session.scalar(
        select(func.count()).select_from(Action).where(Action.status == ActionStatus.PROPOSED)
    )
    products = list(session.scalars(select(Product).order_by(Product.sku)))
    levels = {lvl.sku: lvl.quantity for lvl in session.scalars(select(StockLevel))}
    below_min = [
        (p, levels.get(p.sku, Decimal("0")))
        for p in products
        if p.active and p.min_stock is not None and levels.get(p.sku, Decimal("0")) < p.min_stock
    ]
    incomplete = [p for p in products if not p.active]
    last_import = session.scalar(select(SalesImport).order_by(SalesImport.id.desc()).limit(1))
    return request.app.state.templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "pending": pending,
            "below_min": below_min,
            "incomplete_count": len(incomplete),
            "product_count": len(products),
            "last_import": last_import,
        },
    )
