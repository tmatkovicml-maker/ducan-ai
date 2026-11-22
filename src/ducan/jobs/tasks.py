"""Job bodies shared by the scheduler and the CLI."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from ducan.audit.log import record
from ducan.config import AppConfig
from ducan.inventory.reconciliation import IntegrityIssue, check_integrity
from ducan.models import JobRun
from ducan.pos.csv_pos import CsvPos
from ducan.pos.importer import ImportOutcome, import_from_adapter
from ducan.pricing.job import make_agent, run_pricing
from ducan.replenishment.job import run_replenishment


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


def _today(config: AppConfig) -> date:
    return datetime.now(ZoneInfo(config.business.timezone)).date()


def replenish(config: AppConfig, factory: sessionmaker[Session]) -> JobRun:
    """Nightly: propose purchase orders. Nothing is sent before approval."""
    with factory() as session:
        run = run_replenishment(session, config, _today(config))
        session.commit()
        return run


def pricing(config: AppConfig, factory: sessionmaker[Session]) -> JobRun:
    """Weekly: propose price changes."""
    with factory() as session:
        run = run_pricing(session, config, _today(config), agent=make_agent(config))
        session.commit()
        return run


def rota(config: AppConfig, factory: sessionmaker[Session]) -> JobRun:
    """Weekly: propose next week's rota."""
    from ducan.scheduling.job import run_rota

    with factory() as session:
        run = run_rota(session, config, _today(config))
        session.commit()
        return run


def rota_deadline(config: AppConfig, factory: sessionmaker[Session]) -> JobRun:
    """At the deadline: alert the owner if next week's rota is missing."""
    from ducan.comms.notify import make_notifier
    from ducan.scheduling.job import check_rota_deadline

    with factory() as session:
        run = check_rota_deadline(session, config, _today(config), make_notifier(config.env))
        session.commit()
        return run


def posts(config: AppConfig, factory: sessionmaker[Session]) -> JobRun:
    """Daily: draft posts for calendar entries due in the next two days."""
    from datetime import timedelta

    from sqlalchemy import select

    from ducan.brand import load_brand
    from ducan.comms.posts import PostNotDrafted, draft_post
    from ducan.db import utcnow
    from ducan.models import ContentCalendarEntry

    today = _today(config)
    brand = load_brand(config.env.brand_dir)
    agent = make_agent(config)
    with factory() as session:
        run = JobRun(job="posts", started_at=utcnow())
        session.add(run)
        session.flush()
        proposed, flags = [], []
        due = session.scalars(
            select(ContentCalendarEntry).where(
                ContentCalendarEntry.action_id.is_(None),
                ContentCalendarEntry.publish_on >= today,
                ContentCalendarEntry.publish_on <= today + timedelta(days=2),
            )
        )
        for entry in list(due):
            try:
                proposed.append(draft_post(session, config.business, brand, entry, agent, today).id)
            except PostNotDrafted as exc:
                flags.append(f"Objava {entry.publish_on:%d.%m.}: {exc}")
        run.finished_at = utcnow()
        run.status = "ok"
        run.summary = {"proposed": proposed, "flags": flags}
        record(
            session,
            actor="agent",
            event="job.posts",
            summary=f"Objave: {len(proposed)} prijedloga",
            details=run.summary,
        )
        session.commit()
        return run
