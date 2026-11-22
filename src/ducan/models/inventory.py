"""Stock levels and the movements that produce them."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ducan.db import Base, DecimalText, UTCDateTime, utcnow


class MovementKind(StrEnum):
    """Why stock changed."""

    SALE = "sale"
    RECEIPT = "receipt"
    COUNT_ADJUSTMENT = "count_adjustment"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class StockLevel(Base):
    """Current quantity per SKU; always equals the sum of its movements."""

    __tablename__ = "stock_levels"

    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(DecimalText, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_counted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class StockMovement(Base):
    """An append-only stock change. Corrections are new movements, never edits."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), index=True)
    delta: Mapped[Decimal] = mapped_column(DecimalText)
    kind: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(50))
