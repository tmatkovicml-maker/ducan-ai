"""Content calendar, pasted reviews, the reviewed FAQ and chat escalations.

Personal data is kept to a minimum: a review stores only the display name the
owner pasted (needed to address the reply) and can be deleted; chat stores no
conversation, only questions it could not answer, without names or contacts.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ducan.db import Base, UTCDateTime, utcnow

LANGUAGES = ("hr", "en", "de", "it")


class ContentCalendarEntry(Base):
    """A planned social post: when, about what, in which languages."""

    __tablename__ = "content_calendar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publish_on: Mapped[date] = mapped_column(Date, index=True)
    # opening_hours | local_products | seasonal_essentials | product
    topic: Mapped[str] = mapped_column(String(32))
    skus: Mapped[list[str]] = mapped_column(JSON, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON, default=lambda: list(LANGUAGES))
    # Brand asset keys from brand/brand.yaml to attach (never generated images).
    brand_assets: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("actions.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Review(Base):
    """A Google review pasted by the owner (the Business Profile API comes later)."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="google")
    author_name: Mapped[str | None] = mapped_column(String(100))
    stars: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(2), default="hr")
    review_date: Mapped[date | None] = mapped_column(Date)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("actions.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class FaqEntry(Base):
    """A question and its answers, reviewed by the owner before the chat may use it."""

    __tablename__ = "faq_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_hr: Mapped[str] = mapped_column(Text)
    # {"hr": "...", "en": "...", "de": "...", "it": "..."}; missing languages fall back to hr
    answers: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    keywords: Mapped[str] = mapped_column(Text, default="")  # extra search words, any language
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChatEscalation(Base):
    """A customer question the chat could not answer from reviewed facts."""

    __tablename__ = "chat_escalations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(2))
    reason: Mapped[str] = mapped_column(String(200))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
