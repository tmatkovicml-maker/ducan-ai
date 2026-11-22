"""Stock reconciliation."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from ducan.inventory.reconciliation import apply_physical_count, check_integrity
from ducan.inventory.stock import record_movement
from ducan.models import AuditLog, MovementKind, StockLevel, StockMovement


def test_count_books_differences(catalog):
    record_movement(
        catalog, sku="P4-0001", delta=Decimal("30"), kind=MovementKind.RECEIPT, actor="owner"
    )  # level now 60, movements sum 30 (fixture starts at 30)
    diffs = apply_physical_count(
        catalog, {"P4-0001": Decimal("57"), "P4-0002": Decimal("5")}, "owner", "tjedna"
    )
    by_sku = {d.sku: d for d in diffs}
    assert by_sku["P4-0001"].delta == Decimal("-3")
    assert by_sku["P4-0002"].delta == Decimal("0")
    assert catalog.get(StockLevel, "P4-0001").quantity == Decimal("57")
    adj = catalog.scalars(
        select(StockMovement).where(StockMovement.kind == MovementKind.COUNT_ADJUSTMENT)
    ).all()
    assert len(adj) == 1 and "prebrojano 57" in adj[0].reason
    assert catalog.get(StockLevel, "P4-0002").last_counted_at is not None
    assert catalog.scalar(select(AuditLog).where(AuditLog.event == "stock.counted"))


def test_count_rejects_unknown_and_negative(catalog):
    with pytest.raises(ValueError):
        apply_physical_count(catalog, {"P4-9999": Decimal("1")}, "owner")
    with pytest.raises(ValueError):
        apply_physical_count(catalog, {"P4-0001": Decimal("-1")}, "owner")


def test_integrity_detects_mismatch(catalog):
    # Fixture levels were set directly, without movements: all three non-zero ones mismatch.
    issues = {i.sku for i in check_integrity(catalog)}
    assert issues == {"P4-0001", "P4-0002"}
    catalog.get(StockLevel, "P4-0001").quantity = Decimal("0")
    catalog.get(StockLevel, "P4-0002").quantity = Decimal("0")
    record_movement(
        catalog, sku="P4-0001", delta=Decimal("5"), kind=MovementKind.RECEIPT, actor="owner"
    )
    assert check_integrity(catalog) == []
