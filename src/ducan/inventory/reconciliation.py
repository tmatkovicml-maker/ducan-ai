"""Stock reconciliation.

* :func:`apply_physical_count` compares a physical count with the system and
  books every difference as a ``count_adjustment`` movement with a reason.
* :func:`check_integrity` verifies that each stock level equals the sum of its
  movements (it never "fixes" anything silently).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.db import utcnow
from ducan.inventory.stock import record_movement
from ducan.models import MovementKind, Product, StockLevel, StockMovement


@dataclass(frozen=True)
class CountDifference:
    """Result of comparing one counted SKU with the system."""

    sku: str
    system: Decimal
    counted: Decimal

    @property
    def delta(self) -> Decimal:
        return self.counted - self.system


def apply_physical_count(
    session: Session, counts: dict[str, Decimal], actor: str, note: str = ""
) -> list[CountDifference]:
    """Book differences between counted and system quantities."""
    unknown = [sku for sku in counts if session.get(Product, sku) is None]
    if unknown:
        raise ValueError(f"Nepoznati proizvodi: {', '.join(sorted(unknown))}")
    now = utcnow()
    differences = []
    for sku, counted in counts.items():
        if counted < 0:
            raise ValueError(f"{sku}: prebrojana količina ne može biti negativna.")
        level = session.get(StockLevel, sku)
        system = level.quantity if level else Decimal("0")
        diff = CountDifference(sku=sku, system=system, counted=counted)
        differences.append(diff)
        if diff.delta != 0:
            reason = f"Inventura: sustav {system}, prebrojano {counted}"
            if note:
                reason += f" ({note})"
            record_movement(
                session,
                sku=sku,
                delta=diff.delta,
                kind=MovementKind.COUNT_ADJUSTMENT,
                actor=actor,
                reason=reason,
            )
        level = session.get(StockLevel, sku)
        level.last_counted_at = now
    record(
        session,
        actor=actor,
        event="stock.counted",
        entity_type="stock",
        summary=f"Inventura: {len(counts)} proizvoda, "
        f"{sum(1 for d in differences if d.delta)} razlika",
        details={
            "differences": [
                {"sku": d.sku, "system": str(d.system), "counted": str(d.counted)}
                for d in differences
                if d.delta
            ],
            "note": note,
        },
    )
    return differences


@dataclass(frozen=True)
class IntegrityIssue:
    """A stock level that does not equal the sum of its movements."""

    sku: str
    level: Decimal
    movements_sum: Decimal


def check_integrity(session: Session) -> list[IntegrityIssue]:
    """Return every SKU whose stored level differs from the sum of its movements."""
    sums: dict[str, Decimal] = {}
    for movement in session.scalars(select(StockMovement)):
        sums[movement.sku] = sums.get(movement.sku, Decimal("0")) + movement.delta
    issues = []
    levels = {lvl.sku: lvl.quantity for lvl in session.scalars(select(StockLevel))}
    for sku in sorted(set(sums) | set(levels)):
        level = levels.get(sku, Decimal("0"))
        total = sums.get(sku, Decimal("0"))
        if level != total:
            issues.append(IntegrityIssue(sku=sku, level=level, movements_sum=total))
    return issues
