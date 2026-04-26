"""APScheduler bootstrap — runs inside the FastAPI process.

Step 1 only registers a heartbeat job so we can confirm the scheduler is alive.
Step 2 will add the daily 14:30 K-bar fetch.
"""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _heartbeat() -> None:
    logger.info("[scheduler] heartbeat @ %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)

    scheduler.add_job(
        _heartbeat,
        trigger=IntervalTrigger(seconds=60),
        id="heartbeat",
        replace_existing=True,
        next_run_time=datetime.now(),
    )

    scheduler.start()
    logger.info("Scheduler started (tz=%s)", settings.scheduler_timezone)
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown")
    _scheduler = None
