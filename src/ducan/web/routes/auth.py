"""Login and logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.auth.security import ACTOR, log_in, require_csrf, verify_password
from ducan.config import AppConfig
from ducan.web.deps import get_config, get_session

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    return request.app.state.templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", dependencies=[Depends(require_csrf)])
def login(
    request: Request,
    password: str = Form(...),
    config: AppConfig = Depends(get_config),
    session: Session = Depends(get_session),
) -> Response:
    throttle = request.app.state.login_throttle
    client = request.client.host if request.client else "unknown"
    if throttle.blocked(client):
        return request.app.state.templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Previše neuspjelih pokušaja. Pokušaj ponovno za 15 minuta."},
            status_code=429,
        )
    if not verify_password(password, config.env.admin_password_hash):
        throttle.fail(client)
        record(session, actor="system", event="auth.login_failed", summary="Neuspjela prijava")
        session.commit()
        return request.app.state.templates.TemplateResponse(
            request, "login.html", {"error": "Pogrešna lozinka."}, status_code=401
        )
    throttle.reset(client)
    log_in(request)
    record(session, actor=ACTOR, event="auth.login", summary="Prijava")
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
