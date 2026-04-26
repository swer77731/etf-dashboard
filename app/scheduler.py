"""APScheduler:每天 14:30 自動同步 ETF 清單 + K 棒。

啟動時也會在背景觸發一次「if-needed」的 sync(空 DB → 全 backfill,
有資料 → 增量補今日)。不卡 web server 啟動。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.services import dividend_sync, etf_universe, kbar_sync

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_sync_in_progress = threading.Lock()


def daily_sync_job() -> None:
    """每天 14:30 執行:先掃 ETF 清單(新上市自動進),再同步全市場 K 棒。"""
    if not _sync_in_progress.acquire(blocking=False):
        logger.warning("[daily_sync] previous sync still running — skip this tick")
        return
    try:
        logger.info("[daily_sync] start @ %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        u_stats = etf_universe.sync_universe()
        logger.info("[daily_sync] universe: %s", u_stats)
        k_stats = kbar_sync.sync_all()
        logger.info("[daily_sync] kbar: %s", k_stats)
        d_stats = dividend_sync.sync_all()
        logger.info("[daily_sync] dividend: %s", d_stats)
    except Exception:
        logger.exception("[daily_sync] failed")
    finally:
        _sync_in_progress.release()


def startup_sync_if_needed() -> None:
    """啟動時跑一次:空 DB 或 ETF 清單空 → 全 backfill。

    放在 background thread 跑,不卡 FastAPI startup。
    """
    def _run():
        try:
            from app.database import session_scope
            from sqlalchemy import func, select
            from app.models.etf import ETF

            with session_scope() as session:
                etf_count = session.scalar(select(func.count(ETF.id))) or 0

            logger.info("[startup_sync] etf_list rows = %d", etf_count)
            if etf_count == 0:
                logger.info("[startup_sync] empty DB detected — running full bootstrap")
                daily_sync_job()
            else:
                logger.info("[startup_sync] DB has data — running incremental sync")
                daily_sync_job()
        except Exception:
            logger.exception("[startup_sync] failed")

    t = threading.Thread(target=_run, name="startup-sync", daemon=True)
    t.start()


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)

    # 每天 14:30 (台北時間,設定檔可調) — 全市場同步
    scheduler.add_job(
        daily_sync_job,
        trigger=CronTrigger(
            hour=settings.daily_fetch_hour,
            minute=settings.daily_fetch_minute,
            timezone=settings.scheduler_timezone,
        ),
        id="daily_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started (tz=%s, daily_sync=%02d:%02d)",
        settings.scheduler_timezone,
        settings.daily_fetch_hour,
        settings.daily_fetch_minute,
    )
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown")
    _scheduler = None
