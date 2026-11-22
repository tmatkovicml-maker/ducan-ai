"""Labour constraints for one person's shifts in one week.

The same checks are used by the rota generator (to choose people) and by the
validator (to reject a rota or a swap that breaks a rule). The limits come from
``config/labour.yaml`` and are never guessed: with a missing value nothing is planned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ducan.config import LabourRules

HOUR = Decimal(3600)


class RulesMissing(ValueError):
    """Labour rules are not filled in."""


@dataclass(frozen=True)
class PlannedShift:
    """A shift in local wall-clock time (no shifts past midnight)."""

    staff_id: int
    day: date
    start: time
    end: time
    break_minutes: int = 0

    def interval(self, tz: str) -> tuple[datetime, datetime]:
        zone = ZoneInfo(tz)
        return (
            datetime.combine(self.day, self.start, tzinfo=zone),
            datetime.combine(self.day, self.end, tzinfo=zone),
        )

    def hours(self, tz: str) -> Decimal:
        start, end = self.interval(tz)
        return Decimal(int((end - start).total_seconds())) / HOUR


Availability = dict[int, tuple[time, time]]  # ISO weekday -> window


def fmt_hours(value: Decimal) -> str:
    """6.5 -> '6,5'."""
    return f"{value.normalize():f}".replace(".", ",")


def require_rules(rules: LabourRules) -> None:
    """Raise :class:`RulesMissing` unless every labour value is set."""
    missing = rules.missing()
    if missing:
        raise RulesMissing("U config/labour.yaml nedostaju vrijednosti: " + ", ".join(missing))


def week_bounds(week_start: date, tz: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz)
    start = datetime.combine(week_start, time.min, tzinfo=zone)
    return start, datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=zone)


def check_person(
    *,
    name: str,
    shifts: list[PlannedShift],
    availability: Availability,
    personal_max_week: Decimal | None,
    rules: LabourRules,
    week_start: date,
    tz: str,
    prior: list[PlannedShift] | None = None,
) -> list[str]:
    """Return every rule this person's week would break (empty list = all good)."""
    require_rules(rules)
    assert rules.max_hours_per_day is not None and rules.max_hours_per_week is not None
    assert rules.min_daily_rest_hours is not None and rules.min_weekly_rest_hours is not None
    assert rules.break_after_hours is not None and rules.break_minutes is not None
    problems: list[str] = []
    per_day: dict[date, Decimal] = {}
    for shift in shifts:
        label = f"{name} {shift.day.strftime('%d.%m.')} {shift.start:%H:%M}-{shift.end:%H:%M}"
        if shift.start >= shift.end:
            problems.append(f"{label}: smjena mora završiti istog dana nakon početka.")
            continue
        window = availability.get(shift.day.isoweekday())
        if window is None or shift.start < window[0] or shift.end > window[1]:
            problems.append(f"{label}: izvan dostupnosti.")
        hours = shift.hours(tz)
        per_day[shift.day] = per_day.get(shift.day, Decimal("0")) + hours
        if hours > rules.break_after_hours and shift.break_minutes < rules.break_minutes:
            problems.append(f"{label}: potrebna stanka od najmanje {rules.break_minutes} min.")
    for day, hours in per_day.items():
        if hours > rules.max_hours_per_day:
            problems.append(
                f"{name} {day.strftime('%d.%m.')}: {fmt_hours(hours)} h, najviše "
                f"{fmt_hours(rules.max_hours_per_day)} h dnevno."
            )
    total = sum(per_day.values(), Decimal("0"))
    week_max = rules.max_hours_per_week
    if personal_max_week is not None:
        week_max = min(week_max, personal_max_week)
    if total > week_max:
        problems.append(f"{name}: {fmt_hours(total)} h tjedno, najviše {fmt_hours(week_max)} h.")

    planned = {s.interval(tz) for s in shifts if s.start < s.end}
    intervals = sorted(planned | {s.interval(tz) for s in (prior or []) if s.start < s.end})
    min_rest = timedelta(hours=float(rules.min_daily_rest_hours))
    for (a_start, a_end), (b_start, _b_end) in zip(intervals, intervals[1:], strict=False):
        if (
            (a_start, a_end) in planned or (b_start, _b_end) in planned
        ) and b_start - a_end < min_rest:
            problems.append(
                f"{name}: između smjena {a_end:%d.%m. %H:%M} i {b_start:%d.%m. %H:%M}"
                f" manje od {fmt_hours(rules.min_daily_rest_hours)} h odmora."
            )

    lo, hi = week_bounds(week_start, tz)
    inside = sorted(i for i in planned if lo <= i[0] < hi)
    points = [lo]
    for start, end in inside:
        points.extend([start, end])
    points.append(hi)
    longest = max(points[i + 1] - points[i] for i in range(0, len(points), 2))
    if longest < timedelta(hours=float(rules.min_weekly_rest_hours)):
        problems.append(
            f"{name}: nema neprekinutog tjednog odmora od "
            f"{fmt_hours(rules.min_weekly_rest_hours)} h."
        )
    return problems
