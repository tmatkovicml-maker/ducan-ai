"""Job bodies shared by the scheduler and the CLI."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from ducan.audit.log import record
from ducan.config import AppConfig
from ducan.inventory.reconciliation import IntegrityIssue, check_integrity
from ducan.pos.csv_pos import CsvPos
from ducan.pos.importer import ImportOutcome, import_from_adapter


def import_sales(config: AppConfig, factory: sessionmaker[Session]) -> list[ImportOutcome]:
    """Import every CSV waiting in the POS inbox."""
    adapter = CsvPos(
        config.pos_inbox, config.pos_archive, config.pos_rejected, config.business.timezone
    )
    with factory() as session:
        return import_from_adapter(session, adapter, actor="system")


def check_stock(factory: sessionmaker[Session]) -> list[IntegrityIssue]:
    """Verify stock levels against movements; log any mismatch."""
    with factory() as session:
        issues = check_integrity(session)
        if issues:
            record(
                session,
                actor="system",
                event="stock.integrity_mismatch",
                summary=f"{len(issues)} proizvoda ima neusklađeno stanje",
                details={
                    "issues": [
                        {"sku": i.sku, "level": str(i.level), "movements": str(i.movements_sum)}
                        for i in issues
                    ]
                },
            )
            session.commit()
        return issues
