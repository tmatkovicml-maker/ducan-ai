"""Shift swaps: a person asks to hand a shift to a colleague; the owner decides."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.comms.notify import Notifier
from ducan.config import BusinessConfig
from ducan.db import utcnow
from ducan.models import Shift, Staff, SwapRequest
from ducan.scheduling.rules import RulesMissing, check_person
from ducan.scheduling.service import as_planned, availability_of, published_shifts


class SwapError(ValueError):
    """The swap cannot be made."""


def request_swap(
    session: Session, requester: Staff, shift_id: int, to_staff_id: int, reason: str
) -> SwapRequest:
    """Create a request (from the personal link page)."""
    shift = session.get(Shift, shift_id)
    if shift is None or shift.staff_id != requester.id or shift.status != "active":
        raise SwapError("To nije tvoja aktivna smjena.")
    if shift.day < date.today():
        raise SwapError("Smjena je već prošla.")
    target = session.get(Staff, to_staff_id)
    if target is None or not target.active or target.id == requester.id:
        raise SwapError("Odaberi kolegu ili kolegicu.")
    swap = SwapRequest(
        shift_id=shift.id,
        from_staff_id=requester.id,
        to_staff_id=target.id,
        reason=reason.strip()[:500] or None,
    )
    session.add(swap)
    session.flush()
    record(
        session,
        actor=f"staff:{requester.id}",
        event="swap.requested",
        entity_type="swap",
        entity_id=swap.id,
        summary=f"{requester.name} traži zamjenu s {target.name} za {shift.day:%d.%m.}",
    )
    return swap


def swap_problems(session: Session, swap: SwapRequest, config: BusinessConfig) -> list[str]:
    """Rules the colleague's week would break if they took the shift."""
    shift = session.get(Shift, swap.shift_id)
    target = session.get(Staff, swap.to_staff_id)
    assert shift is not None and target is not None
    week_start = shift.day - timedelta(days=shift.day.weekday())
    own = [
        as_planned(s)
        for s in published_shifts(session, week_start, week_start + timedelta(days=7), target.id)
    ]
    moved = as_planned(shift)
    moved = type(moved)(target.id, moved.day, moved.start, moved.end, moved.break_minutes)
    prior = [
        as_planned(s)
        for s in published_shifts(session, week_start - timedelta(days=2), week_start, target.id)
    ]
    try:
        return check_person(
            name=target.name,
            shifts=[*own, moved],
            availability=availability_of(session, target.id),
            personal_max_week=target.max_hours_week,
            rules=config.labour,
            week_start=week_start,
            tz=config.timezone,
            prior=prior,
        )
    except RulesMissing as exc:
        return [str(exc)]


def decide_swap(
    session: Session,
    swap: SwapRequest,
    approve: bool,
    actor: str,
    config: BusinessConfig,
    notifier: Notifier,
    note: str = "",
) -> None:
    """Approve (re-checked against every rule) or reject; tell both people."""
    if swap.status != "requested":
        raise SwapError("O zahtjevu je već odlučeno.")
    shift = session.get(Shift, swap.shift_id)
    giver = session.get(Staff, swap.from_staff_id)
    taker = session.get(Staff, swap.to_staff_id)
    assert shift is not None and giver is not None and taker is not None
    if approve:
        if shift.status != "active" or shift.staff_id != giver.id:
            raise SwapError("Smjena se u međuvremenu promijenila.")
        problems = swap_problems(session, swap, config)
        if problems:
            raise SwapError("Zamjena bi prekršila pravila: " + "; ".join(problems))
        shift.staff_id = taker.id
    swap.status = "approved" if approve else "rejected"
    swap.decided_at = utcnow()
    swap.decided_by = actor
    swap.note = note.strip() or None
    when = f"{shift.day:%d.%m.} {shift.start:%H:%M}-{shift.end:%H:%M}"
    outcome = "odobrena" if approve else "nije odobrena"
    for person in (giver, taker):
        if person.email:
            notifier.send(
                person.email,
                f"Zamjena smjene {when}: {outcome}",
                f"Bok {person.name},\n\nzamjena smjene {when} ({giver.name} → "
                f"{taker.name}) je {outcome}." + (f"\nNapomena: {note}" if note else ""),
            )
    record(
        session,
        actor=actor,
        event=f"swap.{swap.status}",
        entity_type="swap",
        entity_id=swap.id,
        summary=f"Zamjena {when}: {giver.name} → {taker.name}, {outcome}",
    )
