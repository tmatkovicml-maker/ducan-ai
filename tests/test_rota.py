"""Rota: labour constraints, generator, approval, Thursday deadline, swaps.

The labour values below are test fixtures only, not statements about the law.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select

from ducan.actions import executors, service
from ducan.actions.executors.shift_schedule import ShiftScheduleExecutor
from ducan.config import LabourRules, Staffing
from ducan.models import Action, ActionStatus, ActionType, Shift, Staff, StaffAvailability
from ducan.scheduling.generator import Person, generate, split_span
from ducan.scheduling.job import check_rota_deadline, run_rota
from ducan.scheduling.rules import PlannedShift, RulesMissing, check_person
from ducan.scheduling.service import staff_by_token, validate_rota
from ducan.scheduling.swaps import SwapError, decide_swap, request_swap
from tests.conftest import NOW

TZ = "Europe/Zagreb"
TODAY = date(2026, 11, 18)  # Wednesday
WEEK = date(2026, 11, 23)  # next Monday
D = Decimal
ALL_DAYS = {d: (time(7), time(21)) for d in range(1, 8)}

RULES = LabourRules(
    max_hours_per_day=D("8"),
    max_hours_per_week=D("40"),
    min_daily_rest_hours=D("11"),
    min_weekly_rest_hours=D("24"),
    break_after_hours=D("6"),
    break_minutes=30,
)
STAFFING = Staffing.model_validate(
    {
        "seasons": [
            {
                "name": "Cijela godina",
                "from": "01-01",
                "to": "12-31",
                "hours": {
                    d: "07:00-21:00" for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
                },
                "min_staff": 1,
                "peaks": [{"days": ["sat"], "from": "10:00", "to": "14:00", "min_staff": 2}],
            }
        ]
    }
)


def shift(day_offset, start, end, staff_id=1, brk=30):
    return PlannedShift(
        staff_id,
        date.fromordinal(WEEK.toordinal() + day_offset),
        time.fromisoformat(start),
        time.fromisoformat(end),
        brk,
    )


def problems(shifts, availability=ALL_DAYS, personal=None, prior=None):
    return check_person(
        name="Ana",
        shifts=shifts,
        availability=availability,
        personal_max_week=personal,
        rules=RULES,
        week_start=WEEK,
        tz=TZ,
        prior=prior,
    )


# --- constraints -------------------------------------------------------------------


def test_valid_week_passes():
    assert problems([shift(i, "07:00", "14:00") for i in range(5)]) == []


def test_max_hours_per_day():
    assert any(
        "dnevno" in p for p in problems([shift(0, "07:00", "14:00"), shift(0, "14:00", "21:00")])
    )


def test_max_hours_per_week_and_personal_limit():
    six = [shift(i, "07:00", "14:00") for i in range(6)]  # 42 h
    assert any("tjedno" in p for p in problems(six))
    assert any("najviše 20 h" in p for p in problems(six[:3], personal=D("20")))


def test_daily_rest():
    late_then_early = [shift(0, "14:00", "21:00"), shift(1, "07:00", "14:00")]  # 10 h rest
    assert any("odmora" in p for p in problems(late_then_early))


def test_daily_rest_against_previous_week():
    prior = [PlannedShift(1, date(2026, 11, 22), time(14), time(21), 30)]
    assert any("odmora" in p for p in problems([shift(0, "07:00", "14:00")], prior=prior))


def test_weekly_rest():
    every_day = [shift(i, "07:00", "14:00") for i in range(7)]  # longest gap 17 h
    found = problems(every_day, personal=None)
    assert any("tjednog odmora" in p for p in found)


def test_availability_and_break():
    only_weekend = {6: (time(7), time(21)), 7: (time(7), time(21))}
    assert any(
        "dostupnosti" in p
        for p in problems([shift(0, "07:00", "14:00")], availability=only_weekend)
    )
    assert any("stanka" in p for p in problems([shift(0, "07:00", "14:00", brk=0)]))


def test_missing_rules_raise():
    with pytest.raises(RulesMissing):
        check_person(
            name="Ana",
            shifts=[],
            availability={},
            personal_max_week=None,
            rules=LabourRules(),
            week_start=WEEK,
            tz=TZ,
        )


# --- generator ---------------------------------------------------------------------


def test_split_span():
    assert split_span(time(7), time(21), D("8")) == [(time(7), time(14)), (time(14), time(21))]
    assert split_span(time(8), time(12), D("8")) == [(time(8), time(12))]


def _people():
    return [
        Person(1, "Ana", ALL_DAYS),
        Person(2, "Ivo", ALL_DAYS),
        Person(3, "Mara", ALL_DAYS),
        Person(4, "Luka", {6: (time(7), time(21)), 7: (time(7), time(21))}),
    ]


def test_generator_covers_everything_within_rules():
    plan = generate(WEEK, _people(), STAFFING, RULES, TZ)
    assert plan.gaps == []
    assert len(plan.shifts) == 7 * 2 + 1  # two shifts a day + Saturday peak
    by_person: dict[int, list[PlannedShift]] = {}
    for s, why in plan.shifts:
        by_person.setdefault(s.staff_id, []).append(s)
        assert "najmanje među dostupnima" in why
        assert s.break_minutes == (30 if s.hours(TZ) > 6 else 0)  # 7 h shifts need a break
    people = {p.id: p for p in _people()}
    for staff_id, own in by_person.items():
        p = people[staff_id]
        assert (
            check_person(
                name=p.name,
                shifts=own,
                availability=p.availability,
                personal_max_week=None,
                rules=RULES,
                week_start=WEEK,
                tz=TZ,
            )
            == []
        )


def test_generator_reports_gaps_with_reasons():
    plan = generate(WEEK, [Person(1, "Ana", ALL_DAYS)], STAFFING, RULES, TZ)
    assert plan.gaps
    assert all(g["why"] for g in plan.gaps)


def test_generator_flags_days_without_opening_hours():
    plan = generate(WEEK, _people(), Staffing(), RULES, TZ)
    assert plan.shifts == [] and len(plan.flags) == 7


# --- jobs and approval --------------------------------------------------------------


@pytest.fixture
def team(session):
    for sid, name, email, days in (
        (1, "Ana", "ana@example.test", ALL_DAYS),
        (2, "Ivo", "ivo@example.test", ALL_DAYS),
        (3, "Mara", None, ALL_DAYS),
    ):
        session.add(Staff(id=sid, name=name, email=email, role="zaposlenik"))
        for weekday, (a, b) in days.items():
            session.add(StaffAvailability(staff_id=sid, weekday=weekday, start=a, end=b))
    session.commit()
    return session


@pytest.fixture
def rota_config(app_config):
    app_config.business.labour = RULES
    app_config.business.staffing = STAFFING
    app_config.env.public_base_url = "https://ducan.example.test"
    app_config.env.owner_email = "vlasnik@example.test"
    return app_config


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


def _rota(session):
    return session.scalar(select(Action).where(Action.type == ActionType.SHIFT_SCHEDULE))


def test_rota_job_needs_labour_rules(team, app_config):
    app_config.business.staffing = STAFFING
    run = run_rota(team, app_config, TODAY, NOW)
    assert _rota(team) is None
    assert any("labour.yaml" in f for f in run.summary["flags"])


def test_rota_proposed_approved_published_and_withdrawn(team, rota_config):
    notifier = FakeNotifier()
    executors.register(
        ActionType.SHIFT_SCHEDULE, ShiftScheduleExecutor(notifier, rota_config.env.public_base_url)
    )
    run = run_rota(team, rota_config, TODAY, NOW)
    action = _rota(team)
    assert run.summary["proposed"] == [action.id]
    assert action.payload["week_start"] == "2026-11-23"
    assert team.scalars(select(Shift)).all() == []  # nothing published before approval

    service.approve(team, rota_config.business, action.id, "owner", now=NOW)
    assert action.status == ActionStatus.EXECUTED
    shifts = team.scalars(select(Shift).where(Shift.status == "active")).all()
    assert len(shifts) == 15
    assert action.result["not_notified"] == ["Mara"]  # no email address
    to, _subject, body = notifier.sent[0]
    assert "/smjene/" in body
    token = body.split("/smjene/")[1].split()[0]
    assert staff_by_token(team, token).email == to

    service.rollback(team, action.id, "owner", "krivi tjedan")
    assert team.scalars(select(Shift).where(Shift.status == "active")).all() == []


def test_only_one_rota_per_week(team, rota_config):
    run_rota(team, rota_config, TODAY, NOW)
    run = run_rota(team, rota_config, TODAY, NOW)
    assert any("već postoji" in f for f in run.summary["flags"])


def test_tampered_rota_is_rejected(team, rota_config):
    run_rota(team, rota_config, TODAY, NOW)
    payload = dict(_rota(team).payload)
    payload["shifts"] = [
        *payload["shifts"],
        {"staff_id": 1, "day": "2026-11-23", "start": "07:00", "end": "21:00", "break_minutes": 30},
    ]
    assert not validate_rota(team, payload, rota_config.business).ok
    with pytest.raises(service.ProposalRejected):
        service.propose(
            team,
            rota_config.business,
            action_type=ActionType.SHIFT_SCHEDULE,
            title="x",
            payload=payload,
            now=NOW,
        )


def test_deadline_alert_when_rota_missing(team, rota_config):
    notifier = FakeNotifier()
    run = check_rota_deadline(team, rota_config, TODAY, notifier, NOW)
    assert run.summary["missing"] is True
    assert notifier.sent[0][0] == "vlasnik@example.test"
    assert "23.11.2026." in notifier.sent[0][2]


def test_no_alert_when_rota_exists(team, rota_config):
    run_rota(team, rota_config, TODAY, NOW)
    notifier = FakeNotifier()
    run = check_rota_deadline(team, rota_config, TODAY, notifier, NOW)
    assert run.summary["missing"] is False and notifier.sent == []


# --- swaps -------------------------------------------------------------------------


def _publish(session, rows):
    for staff_id, day, start, end in rows:
        session.add(
            Shift(
                staff_id=staff_id,
                day=day,
                start=time.fromisoformat(start),
                end=time.fromisoformat(end),
                break_minutes=30,
            )
        )
    session.flush()


def test_swap_request_and_approval(team, rota_config, monkeypatch):
    monkeypatch.setattr(
        "ducan.scheduling.swaps.date", type("d", (), {"today": staticmethod(lambda: TODAY)})
    )
    _publish(team, [(1, WEEK, "07:00", "14:00")])
    ana, ivo = team.get(Staff, 1), team.get(Staff, 2)
    shift_row = team.scalars(select(Shift)).first()
    swap = request_swap(team, ana, shift_row.id, ivo.id, "liječnik")
    notifier = FakeNotifier()
    decide_swap(team, swap, True, "owner", rota_config.business, notifier)
    assert shift_row.staff_id == ivo.id and swap.status == "approved"
    assert {n[0] for n in notifier.sent} == {"ana@example.test", "ivo@example.test"}


def test_swap_blocked_by_rules(team, rota_config, monkeypatch):
    monkeypatch.setattr(
        "ducan.scheduling.swaps.date", type("d", (), {"today": staticmethod(lambda: TODAY)})
    )
    # Ivo already works the late shift on Sunday 22.11.; Ana's Monday early shift would
    # leave him 10 h rest.
    _publish(team, [(2, date(2026, 11, 22), "14:00", "21:00"), (1, WEEK, "07:00", "14:00")])
    ana_shift = team.scalars(select(Shift).where(Shift.staff_id == 1)).first()
    swap = request_swap(team, team.get(Staff, 1), ana_shift.id, 2, "")
    with pytest.raises(SwapError, match="odmora"):
        decide_swap(team, swap, True, "owner", rota_config.business, FakeNotifier())
    assert ana_shift.staff_id == 1


def test_cannot_request_someone_elses_shift(team):
    _publish(team, [(2, WEEK, "07:00", "14:00")])
    other = team.scalars(select(Shift)).first()
    with pytest.raises(SwapError):
        request_swap(team, team.get(Staff, 1), other.id, 3, "")
