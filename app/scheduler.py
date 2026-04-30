"""APScheduler — 排程拆分版(紀律 #20 監控)。

排程時間表(timezone Asia/Taipei):
- 每 15 分鐘  : 新聞 sync(news_sync.sync_recent)
- 每天 16:00 : K 棒 + 還原股價(kbar_sync.sync_all)
- 每天 16:30 : CMoney 持股(holdings_sync.sync_etf_holdings_cmoney)
- 每天 20:00 : TWSE 配息預告(dividend_announce_sync.sync_all)
- 每天 23:00 : 健康度總檢查(health_check.daily_health_check)
- 每週日 02:00: dividend 全量 sync(dividend_sync.sync_all)
- 每週一 03:00: etf_universe 同步(etf_universe.sync_universe)

每個 sync 內已落實紀律 #20(record_sync_attempt + missing 清單)。
若 sync 跑完 missing 不為空,scheduler 會 5 分鐘後 one-shot retry 一次,
仍 missing → 留待明天該 cron 再跑(不無限 retry)。

健康度檢查 23:00 掃當天所有 sync_status,標出沒跑 / 失敗 / partial。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.config import settings
from app.services import (
    admin_analytics,
    dividend_announce_sync,
    dividend_sync,
    etf_universe,
    health_check,
    holdings_sync,
    kbar_sync,
    news_sync,
    tg_notify,
)

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_locks: dict[str, threading.Lock] = {
    "kbar": threading.Lock(),
    "dividend": threading.Lock(),
    "universe": threading.Lock(),
    "news": threading.Lock(),
    "announce": threading.Lock(),
    "holdings": threading.Lock(),
    "health": threading.Lock(),
    "daily_report": threading.Lock(),
    "analytics_cleanup": threading.Lock(),
    "capacity_snapshot": threading.Lock(),
}

# 5 分鐘後 retry 用 — APScheduler 有 max_instances=1 + coalesce 防併發
_RETRY_DELAY_MIN = 5


# ─────────────────────────────────────────────────────────────
# 個別 sync wrapper(都是 thread-safe lock-protected)
# ─────────────────────────────────────────────────────────────

def _try_lock(name: str) -> bool:
    if not _locks[name].acquire(blocking=False):
        logger.warning("[scheduler] %s sync still running — skip this tick", name)
        return False
    return True


def _release(name: str) -> None:
    try:
        _locks[name].release()
    except RuntimeError:
        pass


def _schedule_retry(name: str, fn) -> None:
    """5 分鐘後 one-shot retry。"""
    if _scheduler is None:
        return
    run_at = datetime.now() + timedelta(minutes=_RETRY_DELAY_MIN)
    _scheduler.add_job(
        fn,
        trigger=DateTrigger(run_date=run_at, timezone=settings.scheduler_timezone),
        id=f"retry_{name}_{run_at.strftime('%H%M%S')}",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("[scheduler] scheduled retry for %s at %s", name, run_at)


# ─────────────────────────────────────────────────────────────
# Job 函式 — 每個包 lock + 紀律 #20 missing → 5min retry
# ─────────────────────────────────────────────────────────────

def kbar_job(_retry: bool = False) -> None:
    if not _try_lock("kbar"):
        return
    try:
        logger.info("[kbar_job] start (retry=%s)", _retry)
        stats = kbar_sync.sync_all()
        logger.info("[kbar_job] %s", stats)
        if not _retry and stats.get("missing"):
            _schedule_retry("kbar", lambda: kbar_job(_retry=True))
    except Exception:
        logger.exception("[kbar_job] failed")
    finally:
        _release("kbar")


def dividend_job(_retry: bool = False) -> None:
    if not _try_lock("dividend"):
        return
    try:
        logger.info("[dividend_job] start (retry=%s)", _retry)
        stats = dividend_sync.sync_all()
        logger.info("[dividend_job] %s", stats)
        if not _retry and stats.get("missing"):
            _schedule_retry("dividend", lambda: dividend_job(_retry=True))
    except Exception:
        logger.exception("[dividend_job] failed")
    finally:
        _release("dividend")


def universe_job(_retry: bool = False) -> None:
    if not _try_lock("universe"):
        return
    try:
        logger.info("[universe_job] start (retry=%s)", _retry)
        stats = etf_universe.sync_universe()
        logger.info("[universe_job] %s", stats)
        if not _retry and stats.get("missing"):
            _schedule_retry("universe", lambda: universe_job(_retry=True))
    except Exception:
        logger.exception("[universe_job] failed")
    finally:
        _release("universe")


def news_job(_retry: bool = False) -> None:
    if not _try_lock("news"):
        return
    try:
        logger.info("[news_job] start (retry=%s)", _retry)
        stats = news_sync.sync_recent()
        logger.info("[news_job] %s", stats)
        if not _retry and stats.get("missing"):
            _schedule_retry("news", lambda: news_job(_retry=True))
    except Exception:
        logger.exception("[news_job] failed")
    finally:
        _release("news")


def announce_job() -> None:
    if not _try_lock("announce"):
        return
    try:
        logger.info("[announce_job] start")
        stats = dividend_announce_sync.sync_all()
        logger.info("[announce_job] %s", stats)
        # twse_announce 沒 per-ETF missing 概念(批次抓全部),不做 retry
    except Exception:
        logger.exception("[announce_job] failed")
    finally:
        _release("announce")


def holdings_job(_retry: bool = False) -> None:
    if not _try_lock("holdings"):
        return
    try:
        logger.info("[holdings_job] start (retry=%s)", _retry)
        from sqlalchemy import select
        from app.database import session_scope
        from app.models.etf import ETF
        with session_scope() as s:
            active_codes = list(s.scalars(
                select(ETF.code).where(ETF.is_active.is_(True))
                .where(ETF.category != "index")
            ).all())
        stats = holdings_sync.sync_etf_holdings_cmoney(active_codes)
        logger.info("[holdings_job] %s",
                    {k: (len(v) if isinstance(v, list) else v) for k, v in stats.items()})
        if not _retry and stats.get("missing_etfs"):
            _schedule_retry("holdings", lambda: holdings_job(_retry=True))
    except Exception:
        logger.exception("[holdings_job] failed")
    finally:
        _release("holdings")


def health_job() -> None:
    if not _try_lock("health"):
        return
    try:
        logger.info("[health_job] start")
        result = health_check.daily_health_check()
        if not result["ok"]:
            logger.warning("[health_job] NOT healthy: %s", result["summary"])
    except Exception:
        logger.exception("[health_job] failed")
    finally:
        _release("health")


def daily_report_job() -> None:
    """每天 20:00 (Taipei) 推今日 TG 日報給 ADMIN_CHAT_ID。"""
    if not _try_lock("daily_report"):
        return
    try:
        logger.info("[daily_report_job] start")
        text = admin_analytics.build_daily_report()
        ok = tg_notify.send_message(text)
        logger.info("[daily_report_job] sent=%s len=%d", ok, len(text))
    except Exception:
        logger.exception("[daily_report_job] failed")
    finally:
        _release("daily_report")


def capacity_snapshot_job() -> None:
    """每 1 分鐘記一次「過去 5 分鐘真人活躍 session 數」到 online_snapshots。

    紀律 #16:同 admin_analytics 排除 bot UA + 高 session IP。
    輕量(1 個 COUNT(DISTINCT) 查詢 + 1 個 INSERT),不上 lock 也 OK
    但保險起見還是 try_lock,避免 cron 排隊堆積。
    """
    if not _try_lock("capacity_snapshot"):
        return
    try:
        n = admin_analytics.take_capacity_snapshot()
        # 避免 log 太吵 — 只在 n > 0 時記
        if n > 0:
            logger.info("[capacity_snapshot] online=%d", n)
    except Exception:
        logger.exception("[capacity_snapshot] failed")
    finally:
        _release("capacity_snapshot")


def analytics_cleanup_job() -> None:
    """每天 03:00 (Taipei) 刪 90 天前的 analytics / search / compare / online_snapshots。"""
    if not _try_lock("analytics_cleanup"):
        return
    try:
        logger.info("[analytics_cleanup_job] start")
        result = admin_analytics.cleanup_old_logs(retain_days=90)
        logger.info("[analytics_cleanup_job] %s", result)
    except Exception:
        logger.exception("[analytics_cleanup_job] failed")
    finally:
        _release("analytics_cleanup")


# ─────────────────────────────────────────────────────────────
# Startup bootstrap — server 啟動時若 DB 空就跑全量
# ─────────────────────────────────────────────────────────────

def startup_sync_if_needed() -> None:
    """啟動時跑一次:空 DB → 全 backfill;有資料 → 增量同步。

    放 background thread 跑,不卡 FastAPI startup。
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
                logger.info("[startup_sync] empty DB — running full bootstrap")
                universe_job()
                kbar_job()
                dividend_job()
                announce_job()
                news_job()
                holdings_job()
            else:
                logger.info("[startup_sync] DB has data — running incremental")
                kbar_job()
                announce_job()
                news_job()
                holdings_job()
        except Exception:
            logger.exception("[startup_sync] failed")

    t = threading.Thread(target=_run, name="startup-sync", daemon=True)
    t.start()


# ─────────────────────────────────────────────────────────────
# 排程設定
# ─────────────────────────────────────────────────────────────

def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    tz = settings.scheduler_timezone
    scheduler = AsyncIOScheduler(timezone=tz)

    # 排程時間表(可調)
    jobs = [
        # 每 15 分鐘 — 新聞
        ("news_15min", news_job,
         CronTrigger(minute="*/15", timezone=tz)),
        # 每天 16:00 — K 棒 + 還原股價(收盤後)
        ("kbar_daily", kbar_job,
         CronTrigger(hour=16, minute=0, timezone=tz)),
        # 每天 16:30 — CMoney 持股
        ("holdings_daily", holdings_job,
         CronTrigger(hour=16, minute=30, timezone=tz)),
        # 每天 20:00 — TWSE 配息預告
        ("announce_daily", announce_job,
         CronTrigger(hour=20, minute=0, timezone=tz)),
        # 每天 23:00 — 健康度總檢查
        ("health_daily", health_job,
         CronTrigger(hour=23, minute=0, timezone=tz)),
        # 每週日 02:00 — dividend 全量 sync
        ("dividend_weekly", dividend_job,
         CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=tz)),
        # 每週一 03:00 — etf_universe 同步
        ("universe_weekly", universe_job,
         CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=tz)),
        # 每天 03:00 — analytics 90 天清理(避開 02/03 的 weekly 工作衝突,
        # APScheduler max_instances=1 會排隊,週日週一也會跑)
        ("analytics_cleanup_daily", analytics_cleanup_job,
         CronTrigger(hour=3, minute=0, timezone=tz)),
        # 每天 20:00 — 客戶紀錄分析 TG 日報(同時段 announce_daily 跑,但兩者
        # async 各自 lock,日報本身只查本地 analytics_log 很輕)
        ("analytics_daily_report", daily_report_job,
         CronTrigger(hour=20, minute=0, timezone=tz)),
        # 每 1 分鐘 — 容量監控 snapshot(現在/今日尖峰/30天尖峰 用)
        ("capacity_snapshot_min", capacity_snapshot_job,
         CronTrigger(minute="*", timezone=tz)),
    ]

    for job_id, fn, trig in jobs:
        scheduler.add_job(
            fn,
            trigger=trig,
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("[scheduler] job registered: %s", job_id)

    scheduler.start()
    logger.info("Scheduler started (tz=%s, %d jobs)", tz, len(jobs))
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown")
    _scheduler = None
