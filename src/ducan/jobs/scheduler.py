"""Worker process: runs scheduled jobs (one process, separate from the web server)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from ducan.config import get_config
from ducan.db import make_engine, make_sessionmaker
from ducan.jobs.tasks import check_stock, import_sales

log = logging.getLogger("ducan.worker")


def build_scheduler() -> BlockingScheduler:
    """Create the scheduler with every Phase 1 job registered."""
    config = get_config()
    factory = make_sessionmaker(make_engine(config.env.database_url))
    scheduler = BlockingScheduler(timezone=config.business.timezone)
    scheduler.add_job(
        import_sales,
        "interval",
        minutes=config.business.pos.poll_minutes,
        args=[config, factory],
        id="import_sales",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        check_stock,
        "cron",
        hour=3,
        minute=10,
        args=[factory],
        id="check_stock",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    scheduler = build_scheduler()
    log.info("Worker started: %s", [job.id for job in scheduler.get_jobs()])
    scheduler.start()


if __name__ == "__main__":
    main()
