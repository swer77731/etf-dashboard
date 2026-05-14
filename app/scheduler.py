"""APScheduler — 排程拆分版(紀律 #20 監控)。

排程時間表(timezone Asia/Taipei):
- 每天 16:00 : K 棒 + 還原股價(kbar_sync.sync_all)
- 每天 20:00 : TWSE 配息預告(dividend_announce_sync.sync_all)
- 每天 23:00 : 健康度總檢查(health_check.daily_health_check)
- 每週日 02:00: dividend 全量 sync(dividend_sync.sync_all)
- 每週一 03:00: etf_universe 同步(etf_universe.sync_universe)
- 每週一 03:00: 受益人數週更(beneficial_count_sync.sync_all_latest)
                  抓最近 2 週覆寫,FinMind 該 dataset 約週末釋出該週交易日資料
- 每月 5 號 03:00: ETF 規模月更(aum_sync.sync_latest_month)
                  SITCA 月報 1-5 號公告上月,5 號抓最穩
- 每天 04:00/15:00: DB 備份(scripts/backup_to_github.py)
                   推 email-hashed etf.db.gz 到 GitHub 私人 repo,
                   每月 1 號 + 每年 1/1 額外存 monthly/yearly 永久檔

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
    aum_sync,
    beneficial_count_sync,
    dividend_announce_sync,
    dividend_sync,
    etf_universe,
    health_check,
    kbar_sync,
    tg_notify,
    yearly_returns_sync,
)

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_locks: dict[str, threading.Lock] = {
    "kbar": threading.Lock(),
    "dividend": threading.Lock(),
    "universe": threading.Lock(),
    "announce": threading.Lock(),
    "health": threading.Lock(),
    "daily_report": threading.Lock(),
    "analytics_cleanup": threading.Lock(),
    "capacity_snapshot": threading.Lock(),
    "yearly_returns": threading.Lock(),
    "beneficial": threading.Lock(),
    "aum": threading.Lock(),
    "backup": threading.Lock(),
    "data_audit": threading.Lock(),
    "mt_breadth": threading.Lock(),
    "mt_institutional": threading.Lock(),
    "mt_lending": threading.Lock(),
    "mt_margin_short": threading.Lock(),
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


def beneficial_job(_retry: bool = False) -> None:
    """每週一 03:00 (Taipei) — 受益人數週更(全 active ETF 抓最近 2 週)。

    weeks=2 是 Phase 2 約定:覆蓋上週 + 抓本週,UPSERT idempotent。
    紀律 #20:missing 不空 → 5 分鐘後 retry 一次。
    """
    if not _try_lock("beneficial"):
        return
    try:
        logger.info("[beneficial_job] start (retry=%s)", _retry)
        stats = beneficial_count_sync.sync_all_latest()
        logger.info(
            "[beneficial_job] ok=%d / no_data=%d / err=%d / rows=%d",
            stats["ok"], stats.get("no_data", 0),
            stats.get("fetch_error", 0), stats["total_rows_written"],
        )
        # 有 fetch_error 才 retry(no_data 是業務層,不重試)
        if not _retry and stats.get("fetch_error", 0) > 0:
            _schedule_retry("beneficial", lambda: beneficial_job(_retry=True))
    except Exception:
        logger.exception("[beneficial_job] failed")
    finally:
        _release("beneficial")


def aum_job(_retry: bool = False) -> None:
    """每月 5 號 03:00 (Taipei) — SITCA AUM 月更(抓最新可拿月)。

    SITCA 月報延遲 1-2 月,5 號抓最穩(SITCA 月初 1-5 號公告上月)。
    紀律 #20:fetch_error / no_data → 5 分鐘後 retry 一次。
    """
    if not _try_lock("aum"):
        return
    try:
        logger.info("[aum_job] start (retry=%s)", _retry)
        stats = aum_sync.sync_latest_month()
        logger.info(
            "[aum_job] ok=%d / no_data=%d / degraded=%d / err=%d / rows=%d",
            stats["ok"], stats["no_data"], stats.get("degraded", 0),
            stats.get("fetch_error", 0), stats["total_rows_written"],
        )
        # 沒抓到任何月(可能 SITCA 月報還沒釋出 / 網路問題)→ retry 一次
        if not _retry and stats["ok"] == 0:
            _schedule_retry("aum", lambda: aum_job(_retry=True))
    except Exception:
        logger.exception("[aum_job] failed")
    finally:
        _release("aum")


def yearly_returns_job() -> None:
    """每天 04:00 (Taipei) 更新 etf_yearly_returns。

    第一次跑(DB 沒資料)→ backfill 14 支 ETF 全 10 年
    之後每天 → 只重抓今年 + 去年那筆(讓跨年第一天自動把去年 partial 改 0)

    紀律 #16:不動現有 7 個 cron 邏輯,獨立第 8 個 job。
    """
    if not _try_lock("yearly_returns"):
        return
    try:
        if not yearly_returns_sync.has_data():
            logger.info("[yearly_returns_job] first run — full backfill")
        else:
            logger.info("[yearly_returns_job] daily update — refresh current year")
        # sync_all 會把當年 + 歷年都 UPSERT(idempotent),簡化邏輯
        # 跨年第一天:今年 1/1 之後會把去年那筆抓到的 close_price 自動 update,
        # is_partial 也照「year != today.year」邏輯設成 0
        stats = yearly_returns_sync.sync_all()
        logger.info("[yearly_returns_job] %s", stats)
    except Exception:
        logger.exception("[yearly_returns_job] failed")
    finally:
        _release("yearly_returns")


def backup_job() -> None:
    """每天 04:00 / 15:00 (Taipei) — etf.db 備份到 GitHub 私人 repo。

    腳本內已有 try/except 把錯誤吞下,所以這層只負責 lock + log。
    每月 1 號 / 每年 1/1 由腳本自己決定要不要多上 monthly/ yearly/(看當天日期)。
    """
    if not _try_lock("backup"):
        return
    try:
        logger.info("[backup_job] start")
        # 延遲 import 避免 startup 時 httpx 多載一份
        from scripts.backup_to_github import run_backup
        result = run_backup()
        ok_count = sum(1 for u in result.get("uploads", []) if u.get("status") == "success")
        fail_count = sum(1 for u in result.get("uploads", []) if u.get("status") != "success")
        logger.info(
            "[backup_job] done — ok=%s, uploads_success=%d, uploads_failed=%d",
            result.get("ok"), ok_count, fail_count,
        )
    except Exception:
        logger.exception("[backup_job] failed")
    finally:
        _release("backup")


def data_audit_job() -> None:
    """每天 23:30 (Taipei) — 全自動資料健康管家。

    跑完 11 個 check + 自動修能修的 + 修不到 3 次升級為人工待辦。
    結果寫進 sync_status source='data_audit',/admin/analytics 卡片顯示。
    """
    if not _try_lock("data_audit"):
        return
    try:
        logger.info("[data_audit_job] start")
        from app.services import data_audit
        result = data_audit.run_all_checks(auto_fix=True)
        logger.info(
            "[data_audit_job] done — total=%d fixed=%d todo=%d ignored=%d (%ss)",
            result["total"], result["fixed"], result["todo"],
            result["ignored"], result["elapsed_sec"],
        )
    except Exception:
        logger.exception("[data_audit_job] failed")
    finally:
        _release("data_audit")


# ─────────────────────────────────────────────────────────────
# 市場溫度計 5 個 sync(各自 release time)
# ─────────────────────────────────────────────────────────────

def mt_breadth_job(_retry: bool = False) -> None:
    """每天 14:35 — 漲跌家數(TWSE MI_INDEX,收盤 14:30 後即釋出)。"""
    if not _try_lock("mt_breadth"):
        return
    try:
        logger.info("[mt_breadth_job] start (retry=%s)", _retry)
        from app.services import market_temp_sync
        r = market_temp_sync.sync_breadth()
        logger.info("[mt_breadth_job] %s", r)
        if not _retry and r.get("error"):
            _schedule_retry("mt_breadth", lambda: mt_breadth_job(_retry=True))
    except Exception:
        logger.exception("[mt_breadth_job] failed")
    finally:
        _release("mt_breadth")


def mt_institutional_job(_retry: bool = False) -> None:
    """每天 16:05 — 三大法人現貨 + 期貨 + 選擇權(現貨 ~16:00 釋出)。"""
    if not _try_lock("mt_institutional"):
        return
    try:
        logger.info("[mt_institutional_job] start (retry=%s)", _retry)
        from app.services import market_temp_sync
        r = market_temp_sync.sync_institutional()
        logger.info("[mt_institutional_job] %s", r)
        if not _retry and r.get("error"):
            _schedule_retry("mt_institutional", lambda: mt_institutional_job(_retry=True))
    except Exception:
        logger.exception("[mt_institutional_job] failed")
    finally:
        _release("mt_institutional")


def mt_lending_job(_retry: bool = False) -> None:
    """每天 17:35 — 借券當日交易(~17:30 釋出)。"""
    if not _try_lock("mt_lending"):
        return
    try:
        logger.info("[mt_lending_job] start (retry=%s)", _retry)
        from app.services import market_temp_sync
        r = market_temp_sync.sync_lending()
        logger.info("[mt_lending_job] %s", r)
        if not _retry and r.get("error"):
            _schedule_retry("mt_lending", lambda: mt_lending_job(_retry=True))
    except Exception:
        logger.exception("[mt_lending_job] failed")
    finally:
        _release("mt_lending")


def mt_margin_short_job(_retry: bool = False) -> None:
    """每天 19:30 — 融資融券 + 維持率(FinMind 19:00-19:30 才釋出完畢)。

    2026-05-14 從 18:05 改 19:30 — 18:05 搶第一波 FinMind 經常回空 list,
    retry 5min 後 18:10 也常失敗。改 19:30 後成功率大幅提高,即使再失敗
    23:30 data_audit.market_temp_stale 會自動補洞。
    """
    if not _try_lock("mt_margin_short"):
        return
    try:
        logger.info("[mt_margin_short_job] start (retry=%s)", _retry)
        from app.services import market_temp_sync
        r = market_temp_sync.sync_margin_short_and_maintenance()
        logger.info("[mt_margin_short_job] %s", r)
        if not _retry and r.get("error"):
            _schedule_retry("mt_margin_short", lambda: mt_margin_short_job(_retry=True))
    except Exception:
        logger.exception("[mt_margin_short_job] failed")
    finally:
        _release("mt_margin_short")


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

def mt_auto_backfill_if_needed() -> None:
    """容器啟動時檢查市場溫度計 5 table latest date,缺漏 > 3 天 → 自動 backfill。

    紀律 #20:抓不到不寫(sync 內 raise + record_sync_attempt),DB 不會留假資料。
    紀律 #22:每天 23:30 audit + 5 個 cron(14:35/16:05/17:35/18:05)
              保持新鮮度;這個 startup hook 是「重啟後即時補洞」safety net。

    daemon thread 跑,不阻塞 lifespan。
    """
    def _run():
        try:
            from datetime import date as date_type, timedelta
            from sqlalchemy import func, select
            from app.database import session_scope
            from app.models.market_temperature import (
                MarginMaintenance, MarketBreadth, MarginShortTotal,
                SecuritiesLendingDaily, InstitutionalDaily,
            )

            today = date_type.today()
            stale_threshold = today - timedelta(days=3)
            need_backfill = False
            earliest_latest = today

            with session_scope() as session:
                for cls in (MarginMaintenance, MarketBreadth, MarginShortTotal,
                            SecuritiesLendingDaily, InstitutionalDaily):
                    latest = session.scalar(select(func.max(cls.date)))
                    if latest is None or latest < stale_threshold:
                        need_backfill = True
                        if latest:
                            if latest < earliest_latest:
                                earliest_latest = latest
                        else:
                            # 完全沒資料 → 預設補 30 天
                            earliest_latest = today - timedelta(days=30)
                            break

            if not need_backfill:
                logger.info("[mt-auto-backfill] all 5 tables fresh (latest >= %s), skip",
                            stale_threshold)
                return

            # 從最早缺的下一天起補,safety margin 多回推 1 天
            start = earliest_latest - timedelta(days=1)
            end = today
            total = (end - start).days + 1
            logger.info(
                "[mt-auto-backfill] backfilling %s ~ %s (%d days)",
                start, end, total,
            )

            from app.services import market_temp_sync
            d = start
            done = 0
            err = 0
            while d <= end:
                try:
                    r1 = market_temp_sync.sync_breadth(d)
                    r2 = market_temp_sync.sync_institutional(d)
                    r3 = market_temp_sync.sync_lending(d)
                    r4 = market_temp_sync.sync_margin_short_and_maintenance(d)
                    for tag, r in [("breadth", r1), ("inst", r2),
                                   ("lend", r3), ("ms", r4)]:
                        if r.get("error"):
                            err += 1
                            logger.warning(
                                "[mt-auto-backfill] %s/%s err: %s",
                                d, tag, r["error"][:120],
                            )
                except Exception as e:
                    err += 1
                    logger.warning("[mt-auto-backfill] %s raise: %s", d, str(e)[:120])
                done += 1
                d += timedelta(days=1)

            logger.info(
                "[mt-auto-backfill] done — days=%d, errors=%d",
                done, err,
            )
        except Exception:
            logger.exception("[mt-auto-backfill] crashed")

    t = threading.Thread(target=_run, daemon=True, name="mt-auto-backfill")
    t.start()
    logger.info("[mt-auto-backfill] started in background")


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
            else:
                logger.info("[startup_sync] DB has data — running incremental")
                kbar_job()
                announce_job()
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
        # 每天 16:00 — K 棒 + 還原股價(收盤後)
        ("kbar_daily", kbar_job,
         CronTrigger(hour=16, minute=0, timezone=tz)),
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
        # 每週一 03:00 — 受益人數週更(FinMind throttle 1s/call,跟 universe 共
        # 用 throttle 自然序列化,先後不影響)
        ("beneficial_weekly", beneficial_job,
         CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=tz)),
        # 每月 5 號 03:00 — SITCA AUM 月更(SITCA 月報 1-5 號公告上月,5 號最穩)
        ("aum_monthly", aum_job,
         CronTrigger(day=5, hour=3, minute=0, timezone=tz)),
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
        # 每天 04:00 — ETF 歷年含息報酬(給定期定額試算器查 DB 用)
        # 03:00 已被 analytics_cleanup_daily 占,挪 04:00 真正避開
        ("yearly_returns_daily", yearly_returns_job,
         CronTrigger(hour=4, minute=0, timezone=tz)),
        # 每天 04:00 / 15:00 — DB 備份到 GitHub 私人 repo(scripts/backup_to_github.py)
        # 04:00 同時段有 yearly_returns_daily,但兩者各自 lock 不衝突
        # backup 自己處理 monthly / yearly 分支(每月 1 號 / 每年 1/1)
        ("backup_morning", backup_job,
         CronTrigger(hour=4, minute=0, timezone=tz)),
        ("backup_afternoon", backup_job,
         CronTrigger(hour=15, minute=0, timezone=tz)),
        # 每天 23:30 — 資料健康管家(全自動健檢 + 自動修 + 待辦升級)
        # 23:00 已被 health_daily 占,挪 30 分鐘避開
        ("data_audit_daily", data_audit_job,
         CronTrigger(hour=23, minute=30, timezone=tz)),
        # ── 市場溫度計 5 sync(各自釋出時間) ──
        ("mt_breadth_daily", mt_breadth_job,
         CronTrigger(hour=14, minute=35, timezone=tz)),
        ("mt_institutional_daily", mt_institutional_job,
         CronTrigger(hour=16, minute=5, timezone=tz)),
        ("mt_lending_daily", mt_lending_job,
         CronTrigger(hour=17, minute=35, timezone=tz)),
        # 18:05 改 19:30 — FinMind TaiwanStockMarginPurchaseShortSale 通常
        # 19:00-19:30 才釋出完畢,18:05 搶第一波容易碰空 list(2026-05-13 失敗事件)
        ("mt_margin_short_daily", mt_margin_short_job,
         CronTrigger(hour=19, minute=30, timezone=tz)),
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
