"""Content calendar, reviews, FAQ, chat escalations and transparency texts."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.auth.security import require_csrf, require_login
from ducan.comms import disclosure
from ducan.comms.posts import TOPICS, PostNotDrafted, draft_post
from ducan.comms.reviews import ReplyNotDrafted, draft_reply
from ducan.comms.sign import window_sign
from ducan.config import AppConfig
from ducan.models import ChatEscalation, ContentCalendarEntry, FaqEntry, Product, Review
from ducan.models.content import LANGUAGES
from ducan.pricing.job import make_agent
from ducan.web.deps import get_config, get_session

router = APIRouter()


def _today(config: AppConfig) -> date:
    return datetime.now(ZoneInfo(config.business.timezone)).date()


# --- content calendar ----------------------------------------------------------------


def _calendar(
    request: Request, session: Session, error: str | None = None, message: str | None = None
) -> Response:
    entries = list(
        session.scalars(
            select(ContentCalendarEntry).order_by(ContentCalendarEntry.publish_on.desc()).limit(60)
        )
    )
    products = list(
        session.scalars(select(Product).where(Product.active.is_(True)).order_by(Product.name_hr))
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "entries": entries,
            "products": products,
            "topics": TOPICS,
            "languages": LANGUAGES,
            "assets": sorted(request.app.state.brand.files),
            "error": error,
            "message": message,
        },
        status_code=422 if error else 200,
    )


@router.get("/sadrzaj", response_class=HTMLResponse)
def calendar(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    return _calendar(request, session)


@router.post("/sadrzaj", dependencies=[Depends(require_csrf)])
async def add_entry(
    request: Request, actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    form = await request.form()
    try:
        publish_on = date.fromisoformat(str(form.get("publish_on")))
    except ValueError:
        return _calendar(request, session, error="Datum nije ispravan.")
    topic = str(form.get("topic"))
    if topic not in TOPICS:
        return _calendar(request, session, error="Odaberi temu.")
    languages = [lang for lang in LANGUAGES if form.get(f"lang_{lang}") == "on"] or ["hr"]
    skus = [str(v) for v in form.getlist("skus")]
    assets = [str(v) for v in form.getlist("assets")]
    entry = ContentCalendarEntry(
        publish_on=publish_on,
        topic=topic,
        skus=skus,
        languages=languages,
        brand_assets=assets,
        notes=str(form.get("notes") or "").strip() or None,
    )
    session.add(entry)
    session.flush()
    record(
        session,
        actor=actor,
        event="calendar.added",
        entity_type="calendar",
        entity_id=entry.id,
        summary=f"Objava {publish_on:%d.%m.}: {TOPICS[topic]}",
    )
    session.commit()
    return RedirectResponse("/sadrzaj", status_code=303)


@router.post("/sadrzaj/{entry_id}/pripremi", dependencies=[Depends(require_csrf)])
def prepare_post(
    entry_id: int,
    request: Request,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    entry = session.get(ContentCalendarEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Ne postoji.")
    try:
        draft_post(
            session,
            config.business,
            request.app.state.brand,
            entry,
            make_agent(config),
            _today(config),
        )
    except PostNotDrafted as exc:
        session.commit()  # keep audit entries
        return _calendar(request, session, error=str(exc))
    session.commit()
    return RedirectResponse("/akcije", status_code=303)


# --- reviews -------------------------------------------------------------------------


def _reviews(request: Request, session: Session, error: str | None = None) -> Response:
    rows = list(session.scalars(select(Review).order_by(Review.id.desc()).limit(100)))
    return request.app.state.templates.TemplateResponse(
        request,
        "reviews.html",
        {"reviews": rows, "languages": LANGUAGES, "error": error},
        status_code=422 if error else 200,
    )


@router.get("/recenzije", response_class=HTMLResponse)
def reviews(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    return _reviews(request, session)


@router.post("/recenzije", dependencies=[Depends(require_csrf)])
def add_review(
    request: Request,
    text: str = Form(...),
    stars: int = Form(...),
    language: str = Form("hr"),
    author_name: str = Form(""),
    review_date: str = Form(""),
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    if not 1 <= stars <= 5 or not text.strip() or language not in LANGUAGES:
        return _reviews(request, session, error="Upiši tekst recenzije, ocjenu 1–5 i jezik.")
    try:
        when = date.fromisoformat(review_date) if review_date else None
    except ValueError:
        when = None
    review = Review(
        text=text.strip()[:4000],
        stars=stars,
        language=language,
        author_name=author_name.strip()[:100] or None,
        review_date=when,
    )
    session.add(review)
    session.flush()
    record(
        session,
        actor=actor,
        event="review.added",
        entity_type="review",
        entity_id=review.id,
        summary=f"Recenzija {stars}★",
    )
    try:
        draft_reply(session, config.business, review, make_agent(config), _today(config))
    except ReplyNotDrafted as exc:
        session.commit()
        return _reviews(request, session, error=f"Recenzija je spremljena. {exc}")
    session.commit()
    return RedirectResponse("/akcije", status_code=303)


@router.post("/recenzije/{review_id}/pripremi", dependencies=[Depends(require_csrf)])
def retry_reply(
    review_id: int,
    request: Request,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    review = session.get(Review, review_id)
    if review is None:
        raise HTTPException(404, "Ne postoji.")
    try:
        draft_reply(session, config.business, review, make_agent(config), _today(config))
    except ReplyNotDrafted as exc:
        session.commit()
        return _reviews(request, session, error=str(exc))
    session.commit()
    return RedirectResponse("/akcije", status_code=303)


@router.post("/recenzije/{review_id}/obrisi", dependencies=[Depends(require_csrf)])
def delete_review(
    review_id: int, actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    review = session.get(Review, review_id)
    if review is not None:
        session.delete(review)
        record(
            session,
            actor=actor,
            event="review.deleted",
            entity_type="review",
            entity_id=review_id,
            summary="Recenzija obrisana",
        )
        session.commit()
    return RedirectResponse("/recenzije", status_code=303)


# --- FAQ and chat escalations --------------------------------------------------------


def _faq(
    request: Request, session: Session, edit: FaqEntry | None = None, error: str | None = None
) -> Response:
    entries = list(session.scalars(select(FaqEntry).order_by(FaqEntry.id)))
    open_q = list(
        session.scalars(
            select(ChatEscalation)
            .where(ChatEscalation.resolved.is_(False))
            .order_by(ChatEscalation.id.desc())
            .limit(50)
        )
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "faq.html",
        {
            "entries": entries,
            "edit": edit,
            "escalations": open_q,
            "languages": LANGUAGES,
            "error": error,
        },
        status_code=422 if error else 200,
    )


@router.get("/faq", response_class=HTMLResponse)
def faq(
    request: Request,
    uredi: int | None = None,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    return _faq(request, session, session.get(FaqEntry, uredi) if uredi else None)


@router.post("/faq", dependencies=[Depends(require_csrf)])
async def save_faq(
    request: Request, actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    form = await request.form()
    question = str(form.get("question_hr") or "").strip()
    answers = {lang: str(form.get(f"answer_{lang}") or "").strip() for lang in LANGUAGES}
    answers = {k: v for k, v in answers.items() if v}
    if not question or "hr" not in answers:
        return _faq(request, session, error="Pitanje i odgovor na hrvatskom su obavezni.")
    entry_id = form.get("id")
    entry = session.get(FaqEntry, int(entry_id)) if entry_id else FaqEntry()
    if entry is None:
        raise HTTPException(404, "Ne postoji.")
    entry.question_hr = question
    entry.answers = answers
    entry.keywords = str(form.get("keywords") or "").strip()
    entry.reviewed = form.get("reviewed") == "on"
    session.add(entry)
    session.flush()
    record(
        session,
        actor=actor,
        event="faq.saved",
        entity_type="faq",
        entity_id=entry.id,
        summary=f"FAQ: {question[:80]}",
        details={"reviewed": entry.reviewed},
    )
    session.commit()
    return RedirectResponse("/faq", status_code=303)


@router.post("/faq/pitanje/{esc_id}/rijeseno", dependencies=[Depends(require_csrf)])
def resolve_escalation(
    esc_id: int, actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    esc = session.get(ChatEscalation, esc_id)
    if esc is not None:
        esc.resolved = True
        record(
            session,
            actor=actor,
            event="chat.escalation_resolved",
            entity_type="chat_escalation",
            entity_id=esc_id,
            summary="Pitanje riješeno",
        )
        session.commit()
    return RedirectResponse("/faq", status_code=303)


# --- transparency --------------------------------------------------------------------


@router.get("/transparentnost", response_class=HTMLResponse)
def transparency(
    request: Request, _actor: str = Depends(require_login), config: AppConfig = Depends(get_config)
) -> Response:
    base = (config.env.public_base_url or "https://VAŠA-DOMENA").rstrip("/")
    return request.app.state.templates.TemplateResponse(
        request, "transparency.html", {"d": disclosure, "base": base}
    )


@router.get("/transparentnost/natpis.pdf")
def sign_pdf(
    request: Request, _actor: str = Depends(require_login), config: AppConfig = Depends(get_config)
) -> Response:
    try:
        pdf = window_sign(request.app.state.brand, config.env.pdf_font_path)
    except FileNotFoundError as exc:
        raise HTTPException(500, str(exc)) from exc
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="natpis-ai.pdf"'},
    )
