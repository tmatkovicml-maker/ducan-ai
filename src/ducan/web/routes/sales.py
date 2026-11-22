"""Sales imports from the POS (CSV)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.auth.security import require_csrf, require_login
from ducan.config import AppConfig
from ducan.models import Sale, SalesImport
from ducan.pos.csv_pos import CsvPos, parse_csv
from ducan.pos.importer import ImportOutcome, import_batch, import_from_adapter
from ducan.web.deps import get_config, get_session

router = APIRouter(prefix="/prodaja")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def make_csv_pos(config: AppConfig) -> CsvPos:
    """CSV adapter for the configured folders."""
    return CsvPos(
        config.pos_inbox, config.pos_archive, config.pos_rejected, config.business.timezone
    )


def _render(
    request: Request,
    session: Session,
    outcomes: list[ImportOutcome] | None = None,
    error: str | None = None,
) -> Response:
    imports = list(session.scalars(select(SalesImport).order_by(SalesImport.id.desc()).limit(30)))
    sales = list(session.scalars(select(Sale).order_by(Sale.ts.desc()).limit(50)))
    return request.app.state.templates.TemplateResponse(
        request,
        "sales.html",
        {"imports": imports, "sales": sales, "outcomes": outcomes, "error": error},
        status_code=422 if error else 200,
    )


@router.get("", response_class=HTMLResponse)
def sales_overview(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    return _render(request, session)


@router.post("/ucitaj", dependencies=[Depends(require_csrf)])
async def upload(
    request: Request,
    file: UploadFile = File(...),
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return _render(request, session, error="Datoteka je veća od 5 MB.")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "upload.csv")
    batch = parse_csv(content, name=name, tz=config.business.timezone, source="upload")
    outcome = import_batch(session, batch, actor)
    session.commit()
    return _render(request, session, outcomes=[outcome])


@router.post("/obradi", dependencies=[Depends(require_csrf)])
def process_inbox(
    request: Request,
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    outcomes = import_from_adapter(session, make_csv_pos(config), actor)
    return _render(request, session, outcomes=outcomes)
