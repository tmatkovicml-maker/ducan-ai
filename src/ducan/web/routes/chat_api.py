"""Public chat endpoint for the website widget (no login, rate-limited, CORS-restricted)."""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ducan.comms.chat import answer
from ducan.comms.disclosure import CHAT_INTRO, DISCLOSURE
from ducan.config import AppConfig
from ducan.pricing.job import make_agent
from ducan.web.deps import get_config, get_session

router = APIRouter(prefix="/api/chat")
WINDOW, LIMIT = 600, 20
_hits: dict[str, deque[float]] = {}


class ChatIn(BaseModel):
    message: str = Field(max_length=500)
    language: str | None = Field(default=None, max_length=2)


def _cors(request: Request, response: Response, config: AppConfig) -> Response:
    origin = request.headers.get("origin")
    allowed = {
        o.strip().rstrip("/")
        for o in (config.env.chat_allowed_origins or "").split(",")
        if o.strip()
    }
    if origin and origin.rstrip("/") in allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def _limited(client: str) -> bool:
    now = time.monotonic()
    q = _hits.setdefault(client, deque())
    while q and q[0] < now - WINDOW:
        q.popleft()
    if len(q) >= LIMIT:
        return True
    q.append(now)
    return False


@router.options("")
def preflight(request: Request, config: AppConfig = Depends(get_config)) -> Response:
    return _cors(request, Response(status_code=204), config)


@router.get("/uvod")
def intro(
    request: Request, language: str = "hr", config: AppConfig = Depends(get_config)
) -> Response:
    lang = language if language in CHAT_INTRO else "hr"
    return _cors(
        request, JSONResponse({"text": CHAT_INTRO[lang], "disclosure": DISCLOSURE[lang]}), config
    )


@router.post("")
def chat(
    body: ChatIn,
    request: Request,
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    client = request.client.host if request.client else "unknown"
    if _limited(client):
        raise HTTPException(429, "Previše poruka. Pokušajte malo kasnije.")
    today = datetime.now(ZoneInfo(config.business.timezone)).date()
    reply = answer(session, config.business, body.message, body.language, make_agent(config), today)
    session.commit()
    return _cors(
        request,
        JSONResponse(
            {"text": reply.text, "language": reply.language, "escalated": reply.escalated}
        ),
        config,
    )
