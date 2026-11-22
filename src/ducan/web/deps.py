"""Shared FastAPI dependencies and the template environment."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
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
from ducan.formatting import UNKNOWN, fmt_money, fmt_num

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


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


def make_templates(tz: str) -> Jinja2Templates:
    """Template environment with Croatian formatting filters."""
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR), context_processors=[_context])
    zone = ZoneInfo(tz)

    def fmt_dt(value: datetime | None) -> str:
        return "—" if value is None else value.astimezone(zone).strftime("%d.%m.%Y. %H:%M")

    def fmt_opt(value: Any) -> str:
        return UNKNOWN if value in (None, "") else str(value)

    def num_round(value: Decimal | None, places: int = 1) -> str:
        if value is None:
            return UNKNOWN
        return f"{value:.{places}f}".replace(".", ",")

    def hrday(value: str) -> str:
        day = date.fromisoformat(value)
        return f"{('Pon', 'Uto', 'Sri', 'Čet', 'Pet', 'Sub', 'Ned')[day.weekday()]} {day:%d.%m.}"

    templates.env.filters.update(
        hrday=hrday, money=fmt_money, num=fmt_num, dt=fmt_dt, opt=fmt_opt, num_round=num_round
    )
    return templates
