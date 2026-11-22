"""Executor for ``shift_schedule``: publishes the approved rota and notifies staff."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ducan.comms.notify import Notifier
from ducan.models import Action, Shift, Staff
from ducan.scheduling.service import new_link_token, payload_shifts, published_shifts

DAY_NAMES = ("pon", "uto", "sri", "čet", "pet", "sub", "ned")


def _lines(shifts: list[Shift]) -> list[str]:
    return [
        f"- {DAY_NAMES[s.day.weekday()]} {s.day.strftime('%d.%m.')}: "
        f"{s.start:%H:%M}-{s.end:%H:%M}"
        + (f" (stanka {s.break_minutes} min)" if s.break_minutes else "")
        for s in shifts
    ]


class ShiftScheduleExecutor:
    """Create shifts from the approved rota; email each person their shifts."""

    def __init__(self, notifier: Notifier, public_base_url: str | None) -> None:
        self.notifier, self.base_url = notifier, public_base_url

    def execute(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        week_start = date.fromisoformat(action.payload["week_start"])
        week_end = week_start + timedelta(days=7)
        for old in published_shifts(session, week_start, week_end):
            old.status = "cancelled"  # an earlier rota for the same week is replaced
        created: dict[int, list[Shift]] = {}
        for planned in payload_shifts(action.payload):
            shift = Shift(
                staff_id=planned.staff_id,
                day=planned.day,
                start=planned.start,
                end=planned.end,
                break_minutes=planned.break_minutes,
                action_id=action.id,
            )
            session.add(shift)
            created.setdefault(planned.staff_id, []).append(shift)
        session.flush()
        notified, not_notified = [], []
        for staff_id, shifts in created.items():
            staff = session.get(Staff, staff_id)
            if staff is None or not staff.email:
                not_notified.append(staff.name if staff else str(staff_id))
                continue
            body = [
                f"Bok {staff.name},",
                "",
                f"tvoje smjene za tjedan od {week_start.strftime('%d.%m.%Y.')}:",
                "",
                *_lines(sorted(shifts, key=lambda s: (s.day, s.start))),
                "",
            ]
            if self.base_url:
                token = new_link_token(staff)
                body.append(
                    f"Smjene i zahtjev za zamjenu: {self.base_url.rstrip('/')}/smjene/{token}"
                )
            else:
                body.append("Za zamjenu smjene javi se vlasniku.")
            sent = self.notifier.send(
                staff.email, f"Raspored od {week_start.strftime('%d.%m.')}", "\n".join(body)
            )
            (notified if sent else not_notified).append(staff.name)
        return {
            "shifts": sum(len(v) for v in created.values()),
            "notified": notified,
            "not_notified": not_notified,
        }

    def compensate(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        """Withdraw the rota's shifts and tell the people who had them."""
        withdrawn: dict[int, int] = {}
        for shift in session.query(Shift).filter(
            Shift.action_id == action.id, Shift.status == "active"
        ):
            shift.status = "cancelled"
            withdrawn[shift.staff_id] = withdrawn.get(shift.staff_id, 0) + 1
        week = date.fromisoformat(action.payload["week_start"]).strftime("%d.%m.%Y.")
        told = []
        for staff_id in withdrawn:
            staff = session.get(Staff, staff_id)
            if (
                staff
                and staff.email
                and self.notifier.send(
                    staff.email,
                    f"Raspored od {week} je povučen",
                    f"Bok {staff.name},\n\nraspored za tjedan od {week} je povučen. "
                    "Novi raspored stiže uskoro.",
                )
            ):
                told.append(staff.name)
        return {"withdrawn_shifts": sum(withdrawn.values()), "notified": told}
