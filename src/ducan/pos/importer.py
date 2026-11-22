"""Store POS batches as sales and stock movements, idempotently."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.inventory.stock import record_movement
from ducan.models import MovementKind, Product, Sale, SalesImport
from ducan.pos.base import PosAdapter, SaleBatch


@dataclass
class ImportOutcome:
    """Summary of one batch."""

    name: str
    status: str  # imported | duplicate | rejected
    rows_ok: int = 0
    rows_rejected: int = 0
    message: str = ""


def import_batch(session: Session, batch: SaleBatch, actor: str = "system") -> ImportOutcome:
    """Import one batch. A file already imported (same sha256) is skipped."""
    existing = session.scalar(select(SalesImport).where(SalesImport.file_sha256 == batch.sha256))
    if existing is not None:
        record(
            session,
            actor=actor,
            event="sales.duplicate_skipped",
            entity_type="sales_import",
            entity_id=existing.id,
            summary=f"Datoteka {batch.name} je već uvezena.",
        )
        return ImportOutcome(batch.name, "duplicate", message="Već uvezeno.")
    if batch.fatal_error:
        record(
            session,
            actor=actor,
            event="sales.file_rejected",
            entity_type="sales_import",
            summary=f"{batch.name}: {batch.fatal_error}",
        )
        return ImportOutcome(batch.name, "rejected", message=batch.fatal_error)

    imp = SalesImport(
        source=batch.source,
        filename=batch.name,
        file_sha256=batch.sha256,
        ignored_columns=batch.ignored_columns,
    )
    session.add(imp)
    session.flush()
    errors = list(batch.errors)
    ok = 0
    for row_no, row in batch.rows:
        if session.get(Product, row.sku) is None:
            errors.append({"row": row_no, "error": f"nepoznat SKU {row.sku}"})
            continue
        sale = Sale(
            ts=row.ts,
            sku=row.sku,
            quantity=row.quantity,
            gross_amount=row.gross_amount,
            payment_method=row.payment_method,
            import_id=imp.id,
        )
        session.add(sale)
        session.flush()
        record_movement(
            session,
            sku=row.sku,
            delta=-row.quantity,
            kind=MovementKind.SALE,
            actor=actor,
            sale_id=sale.id,
        )
        ok += 1
    imp.rows_ok = ok
    imp.rows_rejected = len(errors)
    imp.errors = sorted(errors, key=lambda e: int(e["row"]))  # type: ignore[arg-type]
    record(
        session,
        actor=actor,
        event="sales.imported",
        entity_type="sales_import",
        entity_id=imp.id,
        summary=f"{batch.name}: {ok} redaka uvezeno, {len(errors)} odbijeno",
        details={"errors": imp.errors, "ignored_columns": batch.ignored_columns},
    )
    return ImportOutcome(batch.name, "imported", ok, len(errors))


def import_from_adapter(
    session: Session, adapter: PosAdapter, actor: str = "system"
) -> list[ImportOutcome]:
    """Import every ready batch from ``adapter``; commits after each batch."""
    outcomes = []
    for batch in adapter.fetch():
        outcome = import_batch(session, batch, actor)
        session.commit()
        adapter.acknowledge(batch, accepted=outcome.status != "rejected")
        outcomes.append(outcome)
    return outcomes
