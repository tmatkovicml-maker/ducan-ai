"""Rota and swap services on top of the database."""

from __future__ import annotations

import hashlib
import secrets
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.config import BusinessConfig
from ducan.models import (
    Action,
    ActionStatus,
    ActionType,
    Shift,
    Staff,
    StaffAvailability,
)
from ducan.scheduling.generator import Person
from ducan.scheduling.rules import Availability, PlannedShift, RulesMissing, check_person
from ducan.validation import ValidationResult

LIVE = (ActionStatus.PROPOSED, ActionStatus.APPROVED, ActionStatus.EXECUTED)


def next_week_start(today: date) -> date:
    """Monday of the week after ``today``."""
    return today + timedelta(days=7 - today.weekday())


def availability_of(session: Session, staff_id: int) -> Availability:
    rows = session.scalars(select(StaffAvailability).where(StaffAvailability.staff_id == staff_id))
    return {r.weekday: (r.start, r.end) for r in rows}


def published_shifts(
    session: Session, start: date, end: date, staff_id: int | None = None
) -> list[Shift]:
    """Active shifts with ``start <= day < end``."""
    stmt = select(Shift).where(Shift.status == "active", Shift.day >= start, Shift.day < end)
    if staff_id is not None:
        stmt = stmt.where(Shift.staff_id == staff_id)
    return list(session.scalars(stmt.order_by(Shift.day, Shift.start)))


def as_planned(shift: Shift) -> PlannedShift:
    return PlannedShift(shift.staff_id, shift.day, shift.start, shift.end, shift.break_minutes)


def load_people(session: Session, week_start: date) -> list[Person]:
    """Active staff with availability and their shifts from the end of the previous week."""
    people = []
    for staff in session.scalars(select(Staff).where(Staff.active.is_(True)).order_by(Staff.id)):
        prior = [
            as_planned(s)
            for s in published_shifts(session, week_start - timedelta(days=2), week_start, staff.id)
        ]
        people.append(
            Person(
                staff.id,
                staff.name,
                availability_of(session, staff.id),
                staff.max_hours_week,
                prior,
            )
        )
    return people


def rota_for_week(session: Session, week_start: date) -> Action | None:
    """The live (proposed, approved or executed) rota action for a week, if any."""
    stmt = (
        select(Action)
        .where(Action.type == ActionType.SHIFT_SCHEDULE, Action.status.in_(LIVE))
        .order_by(Action.id.desc())
    )
    return next(
        (a for a in session.scalars(stmt) if a.payload.get("week_start") == week_start.isoformat()),
        None,
    )


def payload_shifts(payload: dict[str, Any]) -> list[PlannedShift]:
    """Parse shifts from a shift_schedule payload."""
    from datetime import time

    return [
        PlannedShift(
            int(s["staff_id"]),
            date.fromisoformat(s["day"]),
            time.fromisoformat(s["start"]),
            time.fromisoformat(s["end"]),
            int(s.get("break_minutes", 0)),
        )
        for s in payload.get("shifts", [])
    ]


def validate_rota(
    session: Session, payload: dict[str, Any], config: BusinessConfig
) -> ValidationResult:
    """Every shift must belong to an active person and every person must keep every rule."""
    result = ValidationResult()
    try:
        week_start = date.fromisoformat(payload["week_start"])
        shifts = payload_shifts(payload)
    except (KeyError, ValueError) as exc:
        result.errors.append(f"Raspored nije ispravnog oblika: {exc}")
        return result
    if week_start.weekday() != 0:
        result.errors.append("Tjedan rasporeda mora počinjati ponedjeljkom.")
    by_person: dict[int, list[PlannedShift]] = {}
    for shift in shifts:
        if not week_start <= shift.day < week_start + timedelta(days=7):
            result.errors.append(f"Smjena {shift.day} nije u tjednu rasporeda.")
        by_person.setdefault(shift.staff_id, []).append(shift)
    for staff_id, own in by_person.items():
        staff = session.get(Staff, staff_id)
        if staff is None or not staff.active:
            result.errors.append(f"Zaposlenik {staff_id} ne postoji ili nije aktivan.")
            continue
        prior = [
            as_planned(s)
            for s in published_shifts(session, week_start - timedelta(days=2), week_start, staff_id)
        ]
        try:
            result.errors.extend(
                check_person(
                    name=staff.name,
                    shifts=own,
                    availability=availability_of(session, staff_id),
                    personal_max_week=staff.max_hours_week,
                    rules=config.labour,
                    week_start=week_start,
                    tz=config.timezone,
                    prior=prior,
                )
            )
        except RulesMissing as exc:
            result.errors.append(str(exc))
            break
    for gap in payload.get("gaps", []):
        result.warnings.append(f"Nepokriveno: {gap['day']} {gap['start']}-{gap['end']}")
    return result


def new_link_token(staff: Staff) -> str:
    """Create a fresh personal link token; only its hash is stored."""
    token = secrets.token_urlsafe(24)
    staff.link_token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token


def staff_by_token(session: Session, token: str) -> Staff | None:
    digest = hashlib.sha256(token.encode()).hexdigest()
    staff = session.scalar(select(Staff).where(Staff.link_token_hash == digest))
    return staff if staff is not None and staff.active else None


def week_hours(shifts: list[PlannedShift], tz: str) -> Decimal:
    return sum((s.hours(tz) for s in shifts), Decimal("0"))
