"""Competitor prices entered manually by the owner (never scraped)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ducan.db import Base, DecimalText, UTCDateTime, utcnow


class CompetitorPrice(Base):
    """A shelf price seen at a competitor, typed in by the owner."""

    __tablename__ = "competitor_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), index=True)
    competitor: Mapped[str] = mapped_column(String(200))
    price_gross: Mapped[Decimal] = mapped_column(DecimalText)
    observed_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    entered_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    entered_by: Mapped[str] = mapped_column(String(50))
