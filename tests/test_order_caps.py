"""Purchase-order caps and stock rules are enforced (blocked, not flagged)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ducan.actions import service
from ducan.config import SpendingCaps
from ducan.models import ActionStatus, ActionType
from ducan.validation.caps import validate_purchase_order
from tests.conftest import NOW


def _order(*lines, supplier_id=1):
    return {
        "supplier_id": supplier_id,
        "lines": [
            {"sku": sku, "quantity": qty, "unit_price_net": price} for sku, qty, price in lines
        ],
    }


def test_valid_order_passes(catalog, business):
    result = validate_purchase_order(catalog, _order(("P4-0001", "150", "0.40")), business, NOW)
    assert result.ok, result.errors


def test_below_supplier_minimum_is_blocked(catalog, business):
    result = validate_purchase_order(catalog, _order(("P4-0001", "100", "0.40")), business, NOW)
    assert any("minimalne narudžbe" in e for e in result.errors)


def test_over_per_order_cap_is_blocked(catalog, business):
    # 15 x 8.00 + 200 x 0.40 = 200 -> ok; 15 x 8 + 210 x 0.40 = 204 -> blocked
    ok = validate_purchase_order(
        catalog, _order(("P4-0002", "15", "8.00"), ("P4-0001", "200", "0.40")), business, NOW
    )
    assert ok.ok, ok.errors
    over = validate_purchase_order(
        catalog, _order(("P4-0002", "15", "8.00"), ("P4-0001", "210", "0.40")), business, NOW
    )
    assert any("limit po narudžbi" in e for e in over.errors)


def test_zero_caps_block_everything(catalog, business):
    business.spending_caps = SpendingCaps()
    result = validate_purchase_order(catalog, _order(("P4-0001", "150", "0.40")), business, NOW)
    assert any("nije postavljen" in e for e in result.errors)


def test_open_orders_count_towards_max_stock(catalog, business):
    first = service.propose(
        catalog,
        business,
        action_type=ActionType.PURCHASE_ORDER,
        title="Narudžba 1",
        payload=_order(("P4-0002", "15", "8.00")),
        now=NOW,
    )
    first.status = ActionStatus.APPROVED
    first.decided_at = NOW
    catalog.flush()
    ok = validate_purchase_order(catalog, _order(("P4-0001", "200", "0.40")), business, NOW)
    assert ok.ok, ok.errors
    second = service.propose(
        catalog,
        business,
        action_type=ActionType.PURCHASE_ORDER,
        title="Narudžba 2",
        payload=_order(("P4-0001", "200", "0.40")),
        now=NOW,
    )
    second.status = ActionStatus.APPROVED
    second.decided_at = NOW
    catalog.flush()
    # Water: 30 in stock + 200 on order + 11 = 241 > max 240.
    edge = validate_purchase_order(catalog, _order(("P4-0001", "11", "0.40")), business, NOW)
    assert any("maksimalnu zalihu" in e for e in edge.errors)


def test_weekly_cap_blocks_when_exceeded(catalog, business):
    business.spending_caps = SpendingCaps(per_order_net=Decimal("200"), per_week_net=Decimal("150"))
    first = service.propose(
        catalog,
        business,
        action_type=ActionType.PURCHASE_ORDER,
        title="Narudžba 1",
        payload=_order(("P4-0002", "15", "8.00")),
        now=NOW,
    )
    first.status = ActionStatus.EXECUTED
    first.decided_at = NOW
    catalog.flush()
    result = validate_purchase_order(catalog, _order(("P4-0001", "150", "0.40")), business, NOW)
    assert any("Tjedni iznos" in e for e in result.errors)


def test_quantity_above_max_stock_is_blocked(catalog, business):
    # Oil: 5 in stock, max 20 -> 16 is too many.
    result = validate_purchase_order(catalog, _order(("P4-0002", "16", "8.00")), business, NOW)
    assert any("maksimalnu zalihu" in e for e in result.errors)


def test_price_must_match_database(catalog, business):
    result = validate_purchase_order(catalog, _order(("P4-0002", "10", "7.00")), business, NOW)
    assert any("ne odgovara nabavnoj" in e for e in result.errors)


def test_inactive_and_unknown_products_blocked(catalog, business):
    result = validate_purchase_order(
        catalog, _order(("P4-0003", "10", "0.10"), ("P4-9999", "1", "1")), business, NOW
    )
    assert any("P4-0003 nije aktivan" in e for e in result.errors)
    assert any("P4-9999 ne postoji" in e for e in result.errors)


def test_blocked_order_cannot_be_proposed(catalog, business):
    with pytest.raises(service.ProposalRejected):
        service.propose(
            catalog,
            business,
            action_type=ActionType.PURCHASE_ORDER,
            title="Prevelika",
            payload=_order(("P4-0002", "50", "8.00")),
            now=NOW,
        )


def test_cap_rechecked_at_approval(catalog, business):
    """Two orders each under the cap cannot both be approved past the weekly cap."""
    business.spending_caps = SpendingCaps(per_order_net=Decimal("200"), per_week_net=Decimal("130"))
    a = service.propose(
        catalog,
        business,
        action_type=ActionType.PURCHASE_ORDER,
        title="A",
        payload=_order(("P4-0002", "10", "8.00")),
        now=NOW,
    )
    b = service.propose(
        catalog,
        business,
        action_type=ActionType.PURCHASE_ORDER,
        title="B",
        payload=_order(("P4-0001", "150", "0.40")),
        now=NOW,
    )
    service.approve(catalog, business, a.id, "owner", now=NOW)
    assert a.status == ActionStatus.APPROVED  # no purchase-order executor until Phase 2
    service.approve(catalog, business, b.id, "owner", now=NOW)
    assert b.status == ActionStatus.FAILED
    assert "Tjedni iznos" in b.error
