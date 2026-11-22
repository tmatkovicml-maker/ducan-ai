"""Executors perform an approved action and, where possible, compensate it.

Only types with a registered executor can be executed. ``price_change`` is
always available; ``purchase_order`` is registered by :func:`configure` when
SMTP is set up. Other types arrive with their phases. Without an executor an
approved action simply stays "approved".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.orm import Session

from ducan.models import Action, ActionType

if TYPE_CHECKING:
    from ducan.brand import Brand
    from ducan.config import AppConfig


class NotReversible(RuntimeError):
    """The executed action cannot be undone (e.g. a message already sent)."""


class Executor(Protocol):
    """Performs one action type."""

    def execute(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        """Perform the side effect; return a result summary stored on the action."""

    def compensate(self, session: Session, action: Action, actor: str) -> dict[str, Any]:
        """Undo the effect with a compensating change, or raise :class:`NotReversible`."""


_registry: dict[str, Executor] = {}


def _defaults() -> dict[str, Executor]:
    from ducan.actions.executors.price_change import PriceChangeExecutor

    return {ActionType.PRICE_CHANGE: PriceChangeExecutor()}


def configure(config: AppConfig, brand: Brand) -> None:
    """(Re)build the registry for this process from its configuration."""
    from ducan.actions.executors.purchase_order import PurchaseOrderExecutor, SmtpMailer

    _registry.clear()
    _registry.update(_defaults())
    if config.env.smtp_host and config.env.smtp_from:
        _registry[ActionType.PURCHASE_ORDER] = PurchaseOrderExecutor(
            mailer=SmtpMailer(config.env), config=config, brand=brand
        )


def register(action_type: str, executor: Executor) -> None:
    """Register an executor (used by :func:`configure` and tests)."""
    _registry[action_type] = executor


def reset() -> None:
    """Back to the defaults (tests)."""
    _registry.clear()
    _registry.update(_defaults())


def get_executor(action_type: str) -> Executor | None:
    """Return the executor for ``action_type`` or ``None`` if not available."""
    if not _registry:
        reset()
    return _registry.get(action_type)
