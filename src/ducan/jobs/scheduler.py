"""Worker process: runs scheduled jobs (one process, separate from the web server)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from ducan.actions import executors
from ducan.brand import load_brand
from ducan.config import get_config
from ducan.db import make_engine, make_sessionmaker
from ducan.jobs.tasks import (
    check_stock,
    import_sales,
    posts,
    pricing,
    replenish,
    rota,
    rota_deadline,
)

log = logging.getLogger("ducan.worker")


def build_scheduler() -> BlockingScheduler:
    """Create the scheduler with every Phase 1 job registered."""
    config = get_config()
    factory = make_sessionmaker(make_engine(config.env.database_url))
    executors.configure(config, load_brand(config.env.brand_dir))
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
    # After the evening POS export has been imported.
    scheduler.add_job(
        replenish,
        "cron",
        hour=2,
        minute=30,
        args=[config, factory],
        id="replenishment",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        pricing,
        "cron",
        day_of_week="mon",
        hour=3,
        minute=30,
        args=[config, factory],
        id="pricing",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        posts,
        "cron",
        hour=7,
        minute=5,
        args=[config, factory],
        id="posts",
        max_instances=1,
        coalesce=True,
    )
    # Rota for next week on Monday, well before the Thursday deadline.
    scheduler.add_job(
        rota,
        "cron",
        day_of_week="mon",
        hour=6,
        minute=15,
        args=[config, factory],
        id="rota",
        max_instances=1,
        coalesce=True,
    )
    deadline = config.business.staffing.rota_deadline
    scheduler.add_job(
        rota_deadline,
        "cron",
        day_of_week=deadline.weekday,
        hour=deadline.at.hour,
        minute=deadline.at.minute,
        args=[config, factory],
        id="rota_deadline",
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
