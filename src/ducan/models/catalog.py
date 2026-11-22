"""Products, suppliers and price history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ducan.db import Base, DecimalText, UTCDateTime, utcnow


class Supplier(Base):
    """A supplier. Unknown terms stay NULL and are shown as "nepoznato"."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    min_order_net: Mapped[Decimal | None] = mapped_column(DecimalText)
    # Comma-separated ISO weekday numbers, 1=Monday ... 7=Sunday, e.g. "2,5".
    delivery_days: Mapped[str | None] = mapped_column(String(20))
    payment_terms: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(Base):
    """A product in the assortment. Prices are EUR."""

    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(32), primary_key=True)
    name_hr: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(50))
    vat_rate: Mapped[Decimal | None] = mapped_column(DecimalText)  # percent, e.g. 25
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    purchase_price_net: Mapped[Decimal | None] = mapped_column(DecimalText)
    retail_price_gross: Mapped[Decimal | None] = mapped_column(DecimalText)
    min_stock: Mapped[Decimal | None] = mapped_column(DecimalText)
    max_stock: Mapped[Decimal | None] = mapped_column(DecimalText)
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    # Share of sales from the owner's model, percent. Used as a demand prior in Phase 2.
    sales_share_pct: Mapped[Decimal | None] = mapped_column(DecimalText)
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class PriceHistory(Base):
    """Every change of a product's purchase or retail price."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), index=True)
    field: Mapped[str] = mapped_column(String(32))  # purchase_price_net | retail_price_gross
    old_value: Mapped[Decimal | None] = mapped_column(DecimalText)
    new_value: Mapped[Decimal | None] = mapped_column(DecimalText)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    changed_by: Mapped[str] = mapped_column(String(50))
    action_id: Mapped[int | None] = mapped_column(ForeignKey("actions.id"))
    reason: Mapped[str | None] = mapped_column(Text)
