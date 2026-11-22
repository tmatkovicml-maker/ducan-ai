"""Executors perform an approved action and, where possible, compensate it.

Only types with a registered executor can be executed. Phase 1 registers
``price_change``; the others arrive with their phases.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.orm import Session

from ducan.models import Action, ActionType


class NotReversible(RuntimeError):
    """The executed action cannot be undone (e.g. a message already sent)."""


class Executor(Protocol):
    """Performs one action type."""

    def execute(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        """Perform the side effect; return a result summary stored on the action."""

    def compensate(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        """Undo the effect with a compensating change, or raise :class:`NotReversible`."""


def get_executor(action_type: str) -> Executor | None:
    """Return the executor for ``action_type`` or ``None`` if not implemented yet."""
    from ducan.actions.executors.price_change import PriceChangeExecutor

    registry: dict[str, Executor] = {ActionType.PRICE_CHANGE: PriceChangeExecutor()}
    return registry.get(action_type)
