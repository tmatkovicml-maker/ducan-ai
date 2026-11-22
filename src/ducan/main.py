"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from ducan.actions import executors
from ducan.auth.security import SESSION_MAX_AGE, LoginRequired, LoginThrottle
from ducan.brand import load_brand
from ducan.config import AppConfig, get_config
from ducan.db import make_engine, make_sessionmaker
from ducan.web.deps import STATIC_DIR, make_templates
from ducan.web.routes import (
    actions,
    audit,
    auth,
    chat_api,
    competitors,
    content,
    dashboard,
    my_shifts,
    products,
    pwa,
    rota,
    sales,
    staff,
    stock,
    suppliers,
)


def create_app(
    config: AppConfig | None = None, session_factory: sessionmaker[Session] | None = None
) -> FastAPI:
    """Build the app. Fails loudly on bad configuration or missing brand assets."""
    config = config or get_config()
    app = FastAPI(title="Dućan AI", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.brand = load_brand(config.env.brand_dir)
    executors.configure(config, app.state.brand)
    app.state.sessionmaker = session_factory or make_sessionmaker(
        make_engine(config.env.database_url)
    )
    app.state.templates = make_templates(config.business.timezone)
    app.state.login_throttle = LoginThrottle()

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.env.secret_key,
        session_cookie="ducan_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=config.env.cookie_secure,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'",
        )
        return response

    @app.exception_handler(LoginRequired)
    async def _to_login(request: Request, _exc: LoginRequired) -> RedirectResponse:
        if request.headers.get("hx-request"):
            response = RedirectResponse("/login", status_code=200)
            response.headers["HX-Redirect"] = "/login"
            return response
        return RedirectResponse("/login", status_code=303)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for module in (
        auth,
        chat_api,
        content,
        dashboard,
        my_shifts,
        products,
        suppliers,
        staff,
        stock,
        sales,
        actions,
        competitors,
        audit,
        pwa,
        rota,
    ):
        app.include_router(module.router)
    return app
