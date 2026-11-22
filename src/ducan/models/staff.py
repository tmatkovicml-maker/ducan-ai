"""Staff, weekly availability, shifts and shift-swap requests.

Only what is needed to plan the rota is stored. There is no tracking of what
staff do at work; cameras and staff behaviour are out of scope.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ducan.db import Base, DecimalText, UTCDateTime, utcnow


class Staff(Base):
    """A person who can work shifts (owner, partner, seasonal employees)."""

    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(50))  # vlasnik | partner | zaposlenik
    # Personal weekly maximum (e.g. part-time contract); the legal maximum still applies.
    max_hours_week: Mapped[Decimal | None] = mapped_column(DecimalText)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # sha256 of the personal link token for viewing shifts and asking for a swap.
    link_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StaffAvailability(Base):
    """When a person can work on a given weekday (one window per day)."""

    __tablename__ = "staff_availability"
    __table_args__ = (UniqueConstraint("staff_id", "weekday"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # ISO 1=Monday ... 7=Sunday
    start: Mapped[time] = mapped_column(Time)
    end: Mapped[time] = mapped_column(Time)


class Shift(Base):
    """A published shift (created only by an approved rota or swap)."""

    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    start: Mapped[time] = mapped_column(Time)
    end: Mapped[time] = mapped_column(Time)
    break_minutes: Mapped[int] = mapped_column(Integer, default=0)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("actions.id"))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | cancelled
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SwapRequest(Base):
    """A request to hand a shift to a colleague; the owner decides."""

    __tablename__ = "swap_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"))
    from_staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"))
    to_staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="requested")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decided_by: Mapped[str | None] = mapped_column(String(50))
    note: Mapped[str | None] = mapped_column(Text)
