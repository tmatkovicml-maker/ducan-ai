"""Sales imported from the POS. No card numbers or personal data are stored."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ducan.db import Base, DecimalText, UTCDateTime, utcnow


class PaymentMethod(StrEnum):
    """Payment method; only the category, never card details."""

    CASH = "cash"
    CARD = "card"
    OTHER = "other"


class SalesImport(Base):
    """One imported POS file. ``file_sha256`` makes imports idempotent."""

    __tablename__ = "sales_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    rows_ok: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    # Row-level problems: [{"row": 3, "error": "..."}]. Never raw row contents.
    errors: Mapped[list] = mapped_column(JSON, default=list)
    ignored_columns: Mapped[list] = mapped_column(JSON, default=list)


class Sale(Base):
    """One sales line."""

    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), index=True)
    quantity: Mapped[Decimal] = mapped_column(DecimalText)
    gross_amount: Mapped[Decimal] = mapped_column(DecimalText)
    payment_method: Mapped[str] = mapped_column(String(16))
    import_id: Mapped[int] = mapped_column(ForeignKey("sales_imports.id"))
