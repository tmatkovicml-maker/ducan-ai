"""Shared FastAPI dependencies and the template environment."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ducan.auth.security import csrf_token
from ducan.comms.disclosure import DISCLOSURE_HR
from ducan.config import AppConfig

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
UNKNOWN = "nepoznato"


def get_session(request: Request) -> Iterator[Session]:
    """One database session per request; committed by the route."""
    session = request.app.state.sessionmaker()
    try:
        yield session
    finally:
        session.close()


def get_config(request: Request) -> AppConfig:
    """The application's configuration."""
    return request.app.state.config


def _context(request: Request) -> dict[str, Any]:
    return {
        "csrf_token": csrf_token(request),
        "disclosure": DISCLOSURE_HR,
        "brand": request.app.state.brand,
        "path": request.url.path,
    }


def fmt_money(value: Decimal | None) -> str:
    """Croatian money format: 1.234,50 €."""
    if value is None:
        return UNKNOWN
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{text} €"


def fmt_num(value: Decimal | int | None) -> str:
    """Decimal with a comma and no trailing zeros."""
    if value is None:
        return UNKNOWN
    dec = Decimal(value)
    text = format(dec.normalize(), "f") if dec == dec.to_integral() else format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def make_templates(tz: str) -> Jinja2Templates:
    """Template environment with Croatian formatting filters."""
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR), context_processors=[_context])
    zone = ZoneInfo(tz)

    def fmt_dt(value: datetime | None) -> str:
        return "—" if value is None else value.astimezone(zone).strftime("%d.%m.%Y. %H:%M")

    def fmt_opt(value: Any) -> str:
        return UNKNOWN if value in (None, "") else str(value)

    templates.env.filters.update(money=fmt_money, num=fmt_num, dt=fmt_dt, opt=fmt_opt)
    return templates
