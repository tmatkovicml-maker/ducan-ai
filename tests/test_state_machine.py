"""Approval queue state machine and service."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from ducan.actions import service
from ducan.actions.executors import NotReversible
from ducan.actions.state_machine import InvalidTransition, assert_transition, can_transition
from ducan.models import (
    Action,
    ActionStatus,
    ActionType,
    AuditLog,
    PriceHistory,
    Product,
)
from tests.conftest import NOW

S = ActionStatus


@pytest.mark.parametrize(
    ("current", "new", "allowed"),
    [
        (S.PROPOSED, S.APPROVED, True),
        (S.PROPOSED, S.REJECTED, True),
        (S.PROPOSED, S.EXECUTED, False),
        (S.APPROVED, S.EXECUTED, True),
        (S.APPROVED, S.FAILED, True),
        (S.APPROVED, S.REJECTED, False),
        (S.REJECTED, S.APPROVED, False),
        (S.EXECUTED, S.ROLLED_BACK, True),
        (S.EXECUTED, S.PROPOSED, False),
        (S.ROLLED_BACK, S.EXECUTED, False),
        (S.FAILED, S.REJECTED, True),
    ],
)
def test_transitions(current, new, allowed):
    assert can_transition(current, new) is allowed
    if not allowed:
        with pytest.raises(InvalidTransition):
            assert_transition(current, new)


def _propose_price(session, business, new="1.25", old="1.20"):
    return service.propose(
        session,
        business,
        action_type=ActionType.PRICE_CHANGE,
        title="Voda 0,5 l: 1,20 € → 1,25 €",
        payload={"sku": "P4-0001", "old_retail_price_gross": old, "new_retail_price_gross": new},
        references=[{"kind": "product", "id": "P4-0001"}],
        reasoning_summary="Test",
        now=NOW,
    )


def test_propose_approve_execute_rollback(catalog, business):
    session = catalog
    action = _propose_price(session, business)
    assert action.status == S.PROPOSED
    assert action.risk == "low"  # 4.2 % <= 5 %
    assert session.get(Product, "P4-0001").retail_price_gross == Decimal("1.20")

    service.approve(session, business, action.id, "owner", now=NOW)
    assert action.status == S.EXECUTED
    assert session.get(Product, "P4-0001").retail_price_gross == Decimal("1.25")

    service.rollback(session, action.id, "owner", "Greška", now=NOW)
    assert action.status == S.ROLLED_BACK
    assert session.get(Product, "P4-0001").retail_price_gross == Decimal("1.20")
    history = list(session.scalars(select(PriceHistory).order_by(PriceHistory.id)))
    assert [(h.old_value, h.new_value) for h in history] == [
        (Decimal("1.20"), Decimal("1.25")),
        (Decimal("1.25"), Decimal("1.20")),
    ]
    events = [e.event for e in session.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert events == ["action.proposed", "action.approved", "action.executed", "action.rolled_back"]


def test_nothing_executes_before_approval(catalog, business):
    action = _propose_price(catalog, business)
    catalog.commit()
    assert catalog.get(Product, "P4-0001").retail_price_gross == Decimal("1.20")
    with pytest.raises(InvalidTransition):
        service.execute(catalog, business, action, "owner")


def test_reject_is_final(catalog, business):
    action = _propose_price(catalog, business)
    service.reject(catalog, action.id, "owner", "ne")
    assert action.status == S.REJECTED
    with pytest.raises(InvalidTransition):
        service.approve(catalog, business, action.id, "owner")
    assert catalog.get(Product, "P4-0001").retail_price_gross == Decimal("1.20")


def test_stale_proposal_fails_on_execute(catalog, business):
    action = _propose_price(catalog, business)
    catalog.get(Product, "P4-0001").retail_price_gross = Decimal("1.30")  # changed meanwhile
    service.approve(catalog, business, action.id, "owner", now=NOW)
    assert action.status == S.FAILED
    assert "ne odgovara" in action.error
    assert catalog.get(Product, "P4-0001").retail_price_gross == Decimal("1.30")


def test_validator_rejection_is_not_queued_but_audited(catalog, business):
    with pytest.raises(service.ProposalRejected):
        _propose_price(catalog, business, old="9.99")
    assert catalog.scalar(select(Action)) is None
    entry = catalog.scalar(select(AuditLog))
    assert entry.event == "action.rejected_by_validator"


def test_type_without_executor_stays_approved(catalog, business):
    action = service.propose(
        catalog,
        business,
        action_type=ActionType.SOCIAL_POST,
        title="Objava",
        payload={"draft_text": "Otvoreni smo svaki dan."},
        now=NOW,
    )
    assert action.risk == "normal"
    service.approve(catalog, business, action.id, "owner", now=NOW)
    assert action.status == S.APPROVED


def test_rollback_without_executor_is_refused(catalog, business):
    action = Action(
        type=ActionType.CUSTOMER_MESSAGE,
        status=S.EXECUTED,
        title="Poslano",
        payload={},
        proposed_by="agent",
    )
    catalog.add(action)
    catalog.flush()
    with pytest.raises(NotReversible):
        service.rollback(catalog, action.id, "owner", "x")
    assert action.status == S.EXECUTED


def test_approve_all_low_risk_only_touches_low_risk(catalog, business):
    low = _propose_price(catalog, business, new="1.25")
    high = service.propose(
        catalog,
        business,
        action_type=ActionType.PRICE_CHANGE,
        title="Ulje",
        payload={
            "sku": "P4-0002",
            "old_retail_price_gross": "15.90",
            "new_retail_price_gross": "19.90",
        },
        now=NOW,
    )
    assert high.risk == "normal"
    service.approve_all_low_risk(catalog, business, "owner")
    assert low.status == S.EXECUTED
    assert high.status == S.PROPOSED
