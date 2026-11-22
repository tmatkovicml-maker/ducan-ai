"""Weekly rota generator: covers opening hours with available people, within the rules.

Greedy and explainable: shifts are filled in time order; for each place the
available person with the fewest hours so far is chosen, provided their week
still satisfies every labour rule. Every choice and every unfilled gap carries
a short Croatian explanation for the owner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from decimal import Decimal

from ducan.config import WEEKDAYS, LabourRules, Staffing
from ducan.scheduling.rules import (
    Availability,
    PlannedShift,
    check_person,
    fmt_hours,
    require_rules,
)


@dataclass(frozen=True)
class Slot:
    """A block of time that needs ``need`` people."""

    day: date
    start: time
    end: time
    need: int
    label: str


@dataclass
class Person:
    """What the generator needs to know about one staff member."""

    id: int
    name: str
    availability: Availability
    max_hours_week: Decimal | None = None
    prior: list[PlannedShift] = field(default_factory=list)


@dataclass
class RotaPlan:
    """Result of :func:`generate`."""

    week_start: date
    shifts: list[tuple[PlannedShift, str]] = field(default_factory=list)  # (shift, why)
    gaps: list[dict[str, str]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _time(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)


def split_span(start: time, end: time, max_hours: Decimal) -> list[tuple[time, time]]:
    """Split opening hours into the fewest equal shifts no longer than ``max_hours``.

    Boundaries are rounded to 15 minutes.
    """
    total = _minutes(end) - _minutes(start)
    limit = int(max_hours * 60)
    n = max(1, math.ceil(total / limit))
    while True:
        cuts = [_minutes(start) + round(k * total / n / 15) * 15 for k in range(n)]
        cuts.append(_minutes(end))
        pieces = list(zip(cuts, cuts[1:], strict=False))
        if all(b - a <= limit for a, b in pieces):
            return [(_time(a), _time(b)) for a, b in pieces]
        n += 1


def build_slots(
    week_start: date, staffing: Staffing, rules: LabourRules
) -> tuple[list[Slot], list[str]]:
    """Slots for the week from the seasonal opening hours."""
    assert rules.max_hours_per_day is not None
    slots: list[Slot] = []
    flags: list[str] = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        season = staffing.season_for(day)
        if season is None:
            flags.append(
                f"{day.strftime('%d.%m.')}: radno vrijeme nije definirano "
                "(config/opening_hours.yaml)."
            )
            continue
        opening = season.opening(day)
        if opening is None:
            continue  # closed
        pieces = split_span(opening[0], opening[1], rules.max_hours_per_day)
        for i, (a, b) in enumerate(pieces):
            label = (
                "smjena"
                if len(pieces) == 1
                else ("jutarnja", "popodnevna", "večernja", "dodatna")[min(i, 3)]
            )
            slots.append(Slot(day, a, b, season.min_staff, label))
        for peak in season.peaks:
            extra = peak.min_staff - season.min_staff
            if extra <= 0 or WEEKDAYS[day.weekday()] not in peak.days:
                continue
            a, b = max(peak.from_, opening[0]), min(peak.to, opening[1])
            if a >= b:
                continue
            for pa, pb in split_span(a, b, rules.max_hours_per_day):
                slots.append(Slot(day, pa, pb, extra, "pojačanje"))
    return sorted(slots, key=lambda s: (s.day, s.start, s.end)), flags


def _break_for(start: time, end: time, rules: LabourRules) -> int:
    assert rules.break_after_hours is not None and rules.break_minutes is not None
    hours = Decimal(_minutes(end) - _minutes(start)) / 60
    return rules.break_minutes if hours > rules.break_after_hours else 0


def generate(
    week_start: date, people: list[Person], staffing: Staffing, rules: LabourRules, tz: str
) -> RotaPlan:
    """Build a rota proposal. Raises RulesMissing if labour values are not set."""
    require_rules(rules)
    plan = RotaPlan(week_start=week_start)
    slots, plan.flags = build_slots(week_start, staffing, rules)
    assigned: dict[int, list[PlannedShift]] = {p.id: [] for p in people}

    def hours_of(person: Person) -> Decimal:
        return sum((s.hours(tz) for s in assigned[person.id]), Decimal("0"))

    for slot in slots:
        taken: set[int] = set()
        for _place in range(slot.need):
            reasons: list[str] = []
            chosen = None
            for person in sorted(people, key=lambda p: (hours_of(p), p.id)):
                if person.id in taken:
                    continue
                shift = PlannedShift(
                    person.id,
                    slot.day,
                    slot.start,
                    slot.end,
                    _break_for(slot.start, slot.end, rules),
                )
                problems = check_person(
                    name=person.name,
                    shifts=[*assigned[person.id], shift],
                    availability=person.availability,
                    personal_max_week=person.max_hours_week,
                    rules=rules,
                    week_start=week_start,
                    tz=tz,
                    prior=person.prior,
                )
                if problems:
                    reasons.append(problems[0])
                    continue
                chosen = (person, shift)
                break
            if chosen is None:
                plan.gaps.append(
                    {
                        "day": slot.day.isoformat(),
                        "start": f"{slot.start:%H:%M}",
                        "end": f"{slot.end:%H:%M}",
                        "label": slot.label,
                        "why": "; ".join(reasons) or "nema zaposlenika",
                    }
                )
                continue
            person, shift = chosen
            before = hours_of(person)
            assigned[person.id].append(shift)
            taken.add(person.id)
            # Every rule was checked above; the reason says why this person was chosen.
            why = f"dosad {fmt_hours(before)} h, najmanje među dostupnima"
            plan.shifts.append((shift, why))
    return plan
