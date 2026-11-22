"""Approval queue: propose, approve, reject, execute, roll back.

The agent can only call :func:`propose`. Execution happens exclusively after a
human approval, and every step is written to the audit log.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.actions import risk
from ducan.actions.executors import NotReversible, get_executor
from ducan.actions.state_machine import assert_transition
from ducan.audit.log import record
from ducan.config import BusinessConfig
from ducan.db import utcnow
from ducan.models import Action, ActionStatus, ActionType
from ducan.validation import ValidationResult
from ducan.validation.caps import parse_lines, validate_purchase_order
from ducan.validation.price import validate_price_change
from ducan.validation.references import Draft, Reference, validate_draft


class ProposalRejected(ValueError):
    """A proposal failed validation and was not queued."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class ExecutorUnavailable(RuntimeError):
    """No executor exists yet for this action type."""


def _validate(
    session: Session,
    action_type: ActionType,
    payload: dict[str, Any],
    references: list[dict[str, Any]],
    config: BusinessConfig,
    now: datetime,
    exclude_action_id: int | None = None,
) -> ValidationResult:
    result = ValidationResult()
    if action_type == ActionType.PURCHASE_ORDER:
        result.extend(validate_purchase_order(session, payload, config, now, exclude_action_id))
    if action_type == ActionType.PRICE_CHANGE:
        result.extend(validate_price_change(session, payload, config.pricing))
    if action_type == ActionType.SHIFT_SCHEDULE:
        from ducan.scheduling.service import validate_rota

        result.extend(validate_rota(session, payload, config))
    text = payload.get("draft_text")
    if text is not None:
        try:
            refs = [Reference.from_dict(r) for r in references]
        except (KeyError, TypeError):
            result.errors.append("Reference nacrta nisu ispravnog oblika.")
        else:
            money, qty = _computed_values(action_type, payload)
            result.extend(validate_draft(session, Draft(text=text, references=refs), money, qty))
    return result


def _computed_values(
    action_type: ActionType, payload: dict[str, Any]
) -> tuple[set[Decimal], set[Decimal]]:
    """Amounts and quantities derived by code from the payload, allowed in its draft."""
    money: set[Decimal] = set()
    qty: set[Decimal] = set()
    if action_type == ActionType.PURCHASE_ORDER:
        try:
            lines = parse_lines(payload)
        except ValueError:
            return money, qty
        for line in lines:
            money.update({line.unit_price_net, line.total_net})
            qty.add(line.quantity)
        money.add(sum((line.total_net for line in lines), Decimal("0")))
    if action_type == ActionType.PRICE_CHANGE:
        for key in ("old_retail_price_gross", "new_retail_price_gross"):
            if key in payload:
                money.add(Decimal(str(payload[key])))
        for value in payload.get("evidence_amounts", []):
            money.add(Decimal(str(value)))
    return money, qty


def propose(
    session: Session,
    config: BusinessConfig,
    *,
    action_type: ActionType,
    title: str,
    payload: dict[str, Any],
    references: list[dict[str, Any]] | None = None,
    reasoning_summary: str = "",
    data_used: dict[str, Any] | None = None,
    proposed_by: str = "agent",
    now: datetime | None = None,
) -> Action:
    """Validate and queue a proposal. Raises :class:`ProposalRejected` on validation errors."""
    now = now or utcnow()
    references = references or []
    result = _validate(session, action_type, payload, references, config, now)
    if not result.ok:
        record(
            session,
            actor=proposed_by,
            event="action.rejected_by_validator",
            entity_type="action",
            summary=f"Validator odbio prijedlog: {title}",
            details={
                "type": action_type,
                "errors": result.errors,
                "payload": payload,
                "references": references,
            },
        )
        raise ProposalRejected(result.errors)

    action = Action(
        type=action_type,
        status=ActionStatus.PROPOSED,
        title=title,
        payload={**payload, "warnings": result.warnings} if result.warnings else payload,
        references=references,
        reasoning_summary=reasoning_summary,
        data_used=data_used or {},
        risk=risk.classify(session, action_type, payload, config.low_risk),
        proposed_by=proposed_by,
        created_at=now,
    )
    session.add(action)
    session.flush()
    record(
        session,
        actor=proposed_by,
        event="action.proposed",
        entity_type="action",
        entity_id=action.id,
        action_id=action.id,
        summary=title,
        details={
            "type": action_type,
            "risk": action.risk,
            "reasoning": reasoning_summary,
            "data_used": action.data_used,
            "warnings": result.warnings,
        },
    )
    return action


def _get(session: Session, action_id: int) -> Action:
    action = session.get(Action, action_id)
    if action is None:
        raise LookupError(f"Akcija #{action_id} ne postoji.")
    return action


def approve(
    session: Session,
    config: BusinessConfig,
    action_id: int,
    actor: str,
    now: datetime | None = None,
) -> Action:
    """Approve a proposed action and try to execute it immediately."""
    now = now or utcnow()
    action = _get(session, action_id)
    assert_transition(action.status, ActionStatus.APPROVED)
    action.status = ActionStatus.APPROVED
    action.decided_at = now
    action.decided_by = actor
    record(
        session,
        actor=actor,
        event="action.approved",
        entity_type="action",
        entity_id=action.id,
        action_id=action.id,
        summary=action.title,
    )
    session.flush()
    with contextlib.suppress(ExecutorUnavailable):  # stays approved until an executor exists
        execute(session, config, action, actor, now)
    return action


def reject(
    session: Session, action_id: int, actor: str, reason: str = "", now: datetime | None = None
) -> Action:
    """Reject a proposed (or failed) action."""
    action = _get(session, action_id)
    assert_transition(action.status, ActionStatus.REJECTED)
    action.status = ActionStatus.REJECTED
    action.decided_at = now or utcnow()
    action.decided_by = actor
    record(
        session,
        actor=actor,
        event="action.rejected",
        entity_type="action",
        entity_id=action.id,
        action_id=action.id,
        summary=action.title,
        details={"reason": reason},
    )
    return action


def execute(
    session: Session,
    config: BusinessConfig,
    action: Action,
    actor: str,
    now: datetime | None = None,
) -> Action:
    """Execute an approved action after re-validating it against current data."""
    now = now or utcnow()
    if action.status != ActionStatus.APPROVED:
        assert_transition(action.status, ActionStatus.EXECUTED)
    # Re-check against current data first: caps and stock may have changed since proposal.
    result = _validate(
        session,
        ActionType(action.type),
        action.payload,
        action.references,
        config,
        now,
        exclude_action_id=action.id,
    )
    if not result.ok:
        return _fail(session, action, "Ponovna provjera nije prošla: " + "; ".join(result.errors))

    executor = get_executor(action.type)
    if executor is None:
        record(
            session,
            actor="system",
            event="action.awaiting_executor",
            entity_type="action",
            entity_id=action.id,
            action_id=action.id,
            summary="Odobreno; izvršitelj za ovu vrstu akcije još ne postoji.",
        )
        raise ExecutorUnavailable(action.type)
    try:
        with session.begin_nested():
            outcome = executor.execute(session, action, actor)
    except Exception as exc:  # executor errors must never leave a half-done action
        return _fail(session, action, str(exc))

    assert_transition(action.status, ActionStatus.EXECUTED)
    action.status = ActionStatus.EXECUTED
    action.executed_at = now
    action.result = outcome
    record(
        session,
        actor=actor,
        event="action.executed",
        entity_type="action",
        entity_id=action.id,
        action_id=action.id,
        summary=action.title,
        details=outcome,
    )
    return action


def _fail(session: Session, action: Action, error: str) -> Action:
    assert_transition(action.status, ActionStatus.FAILED)
    action.status = ActionStatus.FAILED
    action.error = error
    record(
        session,
        actor="system",
        event="action.failed",
        entity_type="action",
        entity_id=action.id,
        action_id=action.id,
        summary=action.title,
        details={"error": error},
    )
    return action


def approve_all_low_risk(session: Session, config: BusinessConfig, actor: str) -> list[Action]:
    """Approve every proposed low-risk action, one at a time (caps re-checked for each)."""
    stmt = (
        select(Action)
        .where(Action.status == ActionStatus.PROPOSED, Action.risk == risk.LOW)
        .order_by(Action.id)
    )
    approved = []
    for action in list(session.scalars(stmt)):
        approved.append(approve(session, config, action.id, actor))
    return approved


def rollback(
    session: Session, action_id: int, actor: str, reason: str, now: datetime | None = None
) -> Action:
    """Undo an executed action with a compensating change.

    Raises :class:`NotReversible` if the effect cannot be undone (e.g. a sent
    message); that refusal is itself recorded in the audit log.
    """
    action = _get(session, action_id)
    assert_transition(action.status, ActionStatus.ROLLED_BACK)
    executor = get_executor(action.type)
    try:
        if executor is None:
            raise NotReversible("Za ovu vrstu akcije nema izvršitelja.")
        with session.begin_nested():
            outcome = executor.compensate(session, action, actor)
    except NotReversible as exc:
        record(
            session,
            actor=actor,
            event="action.rollback_refused",
            entity_type="action",
            entity_id=action.id,
            action_id=action.id,
            summary=str(exc),
        )
        raise
    action.status = ActionStatus.ROLLED_BACK
    action.rolled_back_at = now or utcnow()
    action.rollback_reason = reason
    record(
        session,
        actor=actor,
        event="action.rolled_back",
        entity_type="action",
        entity_id=action.id,
        action_id=action.id,
        summary=action.title,
        details={"reason": reason, **outcome},
    )
    return action
