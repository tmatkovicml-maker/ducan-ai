"""Allowed status transitions of an action.

proposed ──▶ approved ──▶ executed ──▶ rolled_back
    │            │
    ▼            ▼
rejected       failed ──▶ rejected
"""

from __future__ import annotations

from ducan.models import ActionStatus

S = ActionStatus

TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    S.PROPOSED: frozenset({S.APPROVED, S.REJECTED}),
    S.APPROVED: frozenset({S.EXECUTED, S.FAILED}),
    S.FAILED: frozenset({S.REJECTED}),
    S.EXECUTED: frozenset({S.ROLLED_BACK}),
    S.REJECTED: frozenset(),
    S.ROLLED_BACK: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when an action would move to a status it may not reach."""


def can_transition(current: str, new: str) -> bool:
    """True if ``current -> new`` is allowed."""
    return ActionStatus(new) in TRANSITIONS[ActionStatus(current)]


def assert_transition(current: str, new: str) -> None:
    """Raise :class:`InvalidTransition` unless ``current -> new`` is allowed."""
    if not can_transition(current, new):
        raise InvalidTransition(f"Prijelaz {current} → {new} nije dopušten.")
