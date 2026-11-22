"""Low-risk classification."""

from __future__ import annotations

from ducan.actions.risk import classify
from ducan.config import LowRisk
from ducan.models import Action, ActionStatus, ActionType


def test_unset_thresholds_mean_normal(catalog):
    rules = LowRisk()
    payload = {"sku": "P4-0001", "old_retail_price_gross": "1.20", "new_retail_price_gross": "1.21"}
    assert classify(catalog, ActionType.PRICE_CHANGE, payload, rules) == "normal"


def test_never_low_risk_types(catalog, business):
    for t in (ActionType.SHIFT_SCHEDULE, ActionType.CUSTOMER_MESSAGE, ActionType.SOCIAL_POST):
        assert classify(catalog, t, {}, business.low_risk) == "normal"


def test_review_reply_by_stars(catalog, business):
    assert classify(catalog, ActionType.REVIEW_REPLY, {"stars": 5}, business.low_risk) == "low"
    assert classify(catalog, ActionType.REVIEW_REPLY, {"stars": 2}, business.low_risk) == "normal"


def test_first_order_with_supplier_is_never_low_risk(catalog, business):
    order = {
        "supplier_id": 1,
        "lines": [{"sku": "P4-0001", "quantity": "10", "unit_price_net": "0.40"}],
    }
    assert classify(catalog, ActionType.PURCHASE_ORDER, order, business.low_risk) == "normal"
    catalog.add(
        Action(
            type=ActionType.PURCHASE_ORDER,
            status=ActionStatus.EXECUTED,
            title="prva",
            payload=order,
            proposed_by="agent",
        )
    )
    catalog.flush()
    assert classify(catalog, ActionType.PURCHASE_ORDER, order, business.low_risk) == "low"
