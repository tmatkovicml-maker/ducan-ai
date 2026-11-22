"""Weekly rota proposal and the Thursday deadline alert."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ducan.actions import service
from ducan.audit.log import record
from ducan.comms.notify import Notifier
from ducan.config import AppConfig
from ducan.db import utcnow
from ducan.models import ActionType, JobRun
from ducan.scheduling.generator import generate
from ducan.scheduling.rules import RulesMissing, fmt_hours
from ducan.scheduling.service import load_people, next_week_start, rota_for_week

ROTA_JOB = "rota"
DEADLINE_JOB = "rota_deadline"


def run_rota(
    session: Session, config: AppConfig, today: date, now: datetime | None = None
) -> JobRun:
    """Propose next week's rota (week N+1) unless one already exists."""
    now = now or utcnow()
    business = config.business
    week_start = next_week_start(today)
    run = JobRun(job=ROTA_JOB, started_at=now)
    session.add(run)
    session.flush()
    flags: list[str] = []
    proposed: list[int] = []
    existing = rota_for_week(session, week_start)
    if existing is not None:
        flags.append(f"Raspored za tjedan od {week_start:%d.%m.} već postoji (#{existing.id}).")
    else:
        people = load_people(session, week_start)
        try:
            plan = generate(
                week_start, people, business.staffing, business.labour, business.timezone
            )
        except RulesMissing as exc:
            flags.append(f"{exc}. Raspored se ne predlaže dok ih ne upišeš.")
            plan = None
        if plan is not None:
            flags.extend(plan.flags)
            if not people:
                flags.append("Nema aktivnog osoblja s upisanom dostupnošću.")
            names = {p.id: p.name for p in people}
            hours: dict[int, Decimal] = {}
            for shift, _why in plan.shifts:
                hours[shift.staff_id] = hours.get(shift.staff_id, Decimal("0")) + shift.hours(
                    business.timezone
                )
            summary = ", ".join(f"{names[i]} {fmt_hours(h)} h" for i, h in hours.items())
            gaps = f" Nepokriveno: {len(plan.gaps)} termina." if plan.gaps else ""
            if plan.shifts or plan.gaps:
                try:
                    action = service.propose(
                        session,
                        business,
                        action_type=ActionType.SHIFT_SCHEDULE,
                        title=f"tjedan {week_start:%d.%m.}–"
                        f"{week_start + timedelta(days=6):%d.%m.%Y.}",
                        payload={
                            "week_start": week_start.isoformat(),
                            "shifts": [
                                {
                                    "staff_id": s.staff_id,
                                    "staff_name": names[s.staff_id],
                                    "day": s.day.isoformat(),
                                    "start": f"{s.start:%H:%M}",
                                    "end": f"{s.end:%H:%M}",
                                    "break_minutes": s.break_minutes,
                                    "why": why,
                                }
                                for s, why in plan.shifts
                            ],
                            "gaps": plan.gaps,
                        },
                        reasoning_summary=(
                            f"Sati po osobi: {summary or 'nitko'}.{gaps} "
                            "Uz svaku smjenu piše zašto je odabrana ta osoba; "
                            "sva pravila rada su provjerena."
                        ),
                        data_used={
                            "availability": {
                                names[p.id]: {
                                    str(d): f"{a:%H:%M}-{b:%H:%M}"
                                    for d, (a, b) in p.availability.items()
                                }
                                for p in people
                            }
                        },
                        proposed_by="agent",
                        now=now,
                    )
                    proposed.append(action.id)
                except service.ProposalRejected as exc:
                    flags.append(f"Prijedlog rasporeda odbijen u provjeri: {exc}")
    run.finished_at = utcnow()
    run.status = "ok"
    run.summary = {"week_start": week_start.isoformat(), "proposed": proposed, "flags": flags}
    record(
        session,
        actor="agent",
        event="job.rota",
        summary=f"Raspored za {week_start:%d.%m.}: {len(proposed)} prijedloga",
        details=run.summary,
    )
    return run


def check_rota_deadline(
    session: Session,
    config: AppConfig,
    today: date,
    notifier: Notifier,
    now: datetime | None = None,
) -> JobRun:
    """Run at the deadline (Thursday 12:00): alert the owner if next week has no rota."""
    week_start = next_week_start(today)
    run = JobRun(job=DEADLINE_JOB, started_at=now or utcnow())
    session.add(run)
    session.flush()
    missing = rota_for_week(session, week_start) is None
    flags = []
    if missing:
        text = (
            f"Raspored za tjedan od {week_start:%d.%m.%Y.} još nije predložen, a rok je "
            f"četvrtak u {config.business.staffing.rota_deadline.at:%H:%M}."
        )
        flags.append(text)
        sent = False
        if config.env.owner_email:
            sent = notifier.send(config.env.owner_email, "Upozorenje: nema rasporeda", text)
        record(
            session,
            actor="system",
            event="rota.deadline_missed",
            summary=text,
            details={"week_start": week_start.isoformat(), "emailed": sent},
        )
    run.finished_at = utcnow()
    run.status = "ok"
    run.summary = {"week_start": week_start.isoformat(), "missing": missing, "flags": flags}
    return run
