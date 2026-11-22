"""Audit log viewer."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.auth.security import require_login
from ducan.models import AuditLog
from ducan.web.deps import get_session

router = APIRouter(prefix="/audit")
PAGE_SIZE = 50


@router.get("", response_class=HTMLResponse)
def audit_log(
    request: Request,
    page: int = 1,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    page = max(page, 1)
    entries = list(
        session.scalars(
            select(AuditLog)
            .order_by(AuditLog.id.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE + 1)
        )
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "audit.html",
        {"entries": entries[:PAGE_SIZE], "page": page, "has_next": len(entries) > PAGE_SIZE},
    )
