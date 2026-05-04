"""ETF 受益人數歷史同步 — FinMind TaiwanStockHoldingSharesPer 週更。

策略:
- 每 ETF 1 call 拿全期間(FinMind 該 dataset 一次回完整 date range)
- 過濾 HoldingSharesLevel='total' 取 people
- count <= 0 → 不寫(紀律 #20 + user spec「絕對不寫 0」)
- 連續週 |Δ| > 30% → log 警示但仍寫入(資料源權威 — FinMind 是 TWSE/SITCA 公開)
- backfill 全 active ETF 跑完 → 一次 record_sync_attempt(missing=...)
- weekly cron 跑 sync_all_latest 抓最新 1 週

紀律 #20:
- 抓不到 / 0 row / 異常 → 該 etf_code 入 missing_items
- 失敗時 last_success_at 不變

紀律 #18(引用):
- finmind.request 內建 throttle (1s/call),全市場 255 ETF ≈ 4.5 分鐘
- 50% quota 紅線(我們限 50%,共用方案)由 finmind.request 自動退讓
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import session_scope
from app.models.etf import ETF
from app.models.etf_beneficial_count import EtfBeneficialCount
from app.services import finmind
from app.services.sync_status import record_sync_attempt

logger = logging.getLogger(__name__)

SYNC_SOURCE = "finmind_beneficial"
ANOMALY_THRESHOLD = 0.30  # ±30%(user spec)
DEFAULT_BACKFILL_WEEKS = 52  # 1 年


# ─────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────

def _parse_total_rows(rows: list[dict]) -> dict[str, int]:
    """從 FinMind response 抽出 HoldingSharesLevel='total' 的 (date, people)。

    回 {date_str: people_count}(去重,同 date 多 row 取最後一筆,正常情況不會有)。
    跳過 count <= 0 / 解析失敗 row。
    """
    out: dict[str, int] = {}
    for r in rows:
        if r.get("HoldingSharesLevel") != "total":
            continue
        try:
            people = int(r["people"])
        except (KeyError, ValueError, TypeError):
            continue
        if people <= 0:
            continue
        date_str = r.get("date")
        if not date_str:
            continue
        out[date_str] = people
    return out


def _is_anomalous(prev: int, curr: int) -> bool:
    """連續週變化超過 ±30% 視為異常。prev=0 / None 不檢查(首筆)。"""
    if not prev:
        return False
    return abs(curr - prev) / prev > ANOMALY_THRESHOLD


# ─────────────────────────────────────────────────────────────
# Persist
# ─────────────────────────────────────────────────────────────

def _upsert(etf_code: str, week_counts: dict[str, int]) -> int:
    """UPSERT 多筆 (etf_code, week_date, count)。回實際寫入 row 數。

    SQLite ON CONFLICT (etf_code, week_date) DO UPDATE — idempotent。
    順帶 anomaly check(僅 log,仍寫入)。
    """
    if not week_counts:
        return 0

    sorted_dates = sorted(week_counts.keys())
    payload = []
    prev_count: int | None = None
    for date_str in sorted_dates:
        count = week_counts[date_str]
        try:
            wd = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning("[beneficial][%s] 日期格式錯誤: %r", etf_code, date_str)
            continue
        if prev_count is not None and _is_anomalous(prev_count, count):
            delta = (count - prev_count) / prev_count
            logger.warning(
                "[beneficial][%s] %s 連續週異常 ±%.1f%% (prev=%d → curr=%d)",
                etf_code, date_str, delta * 100, prev_count, count,
            )
        payload.append({
            "etf_code": etf_code,
            "week_date": wd,
            "count": count,
        })
        prev_count = count

    if not payload:
        return 0

    with session_scope() as s:
        for chunk_start in range(0, len(payload), 200):
            chunk = payload[chunk_start:chunk_start + 200]
            stmt = sqlite_insert(EtfBeneficialCount).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["etf_code", "week_date"],
                set_={
                    "count": stmt.excluded.count,
                    "fetched_at": datetime.now(),
                },
            )
            s.execute(stmt)
    return len(payload)


# ─────────────────────────────────────────────────────────────
# 單 ETF 抓取
# ─────────────────────────────────────────────────────────────

def backfill_one(etf_code: str, weeks: int = DEFAULT_BACKFILL_WEEKS) -> dict:
    """抓單一 ETF N 週歷史 → 寫 DB。回 stats dict。

    Returns:
        {"code": "0050", "rows_written": 52, "skipped": 0,
         "earliest": "2025-05-09", "latest": "2026-04-30"} 或
        {"code": "0050", "rows_written": 0, "reason": "no_data" | "no_total_rows" | "fetch_error"}
    """
    today = date.today()
    start = today - timedelta(weeks=weeks + 1)  # 多抓 1 週 buffer
    try:
        rows = finmind.request(
            "TaiwanStockHoldingSharesPer",
            data_id=etf_code,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=today.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        logger.warning("[beneficial][%s] fetch error: %s", etf_code, e)
        return {"code": etf_code, "rows_written": 0, "reason": "fetch_error",
                "error": str(e)[:200]}

    if not rows:
        return {"code": etf_code, "rows_written": 0, "reason": "no_data"}

    counts = _parse_total_rows(rows)
    if not counts:
        return {"code": etf_code, "rows_written": 0, "reason": "no_total_rows"}

    n = _upsert(etf_code, counts)
    sorted_dates = sorted(counts.keys())
    return {
        "code": etf_code,
        "rows_written": n,
        "skipped": 0,
        "earliest": sorted_dates[0],
        "latest": sorted_dates[-1],
    }


def sync_one_latest_week(etf_code: str) -> dict:
    """Cron 用:抓最新 2 週(留 buffer 兼做 anomaly check)。"""
    return backfill_one(etf_code, weeks=2)


# ─────────────────────────────────────────────────────────────
# 全市場(backfill / cron 共用)
# ─────────────────────────────────────────────────────────────

def _list_active_codes() -> list[str]:
    """取所有 active + 非指數類 ETF code。"""
    with session_scope() as s:
        rows = s.scalars(
            select(ETF.code)
            .where(ETF.is_active.is_(True))
            .where(ETF.category != "index")
            .order_by(ETF.code.asc())
        ).all()
    return list(rows)


def backfill_all(weeks: int = DEFAULT_BACKFILL_WEEKS,
                 codes: list[str] | None = None) -> dict:
    """全市場 backfill。回完整 stats(給 backfill script + admin endpoint 顯示)。

    紀律 #20:結尾呼叫 record_sync_attempt(SYNC_SOURCE, missing=...)。
    """
    if codes is None:
        codes = _list_active_codes()
    expected = len(codes)

    stats = {
        "expected": expected,
        "ok": 0,            # 至少寫進 1 row
        "no_data": 0,       # FinMind 回空
        "no_total_rows": 0, # 有 row 但沒 total level
        "fetch_error": 0,
        "total_rows_written": 0,
        "errors": [],       # [{code, error}]
    }
    missing: list[str] = []
    per_code: dict[str, dict] = {}

    logger.info("[beneficial][backfill] start: %d ETFs × %d weeks", expected, weeks)
    for i, code in enumerate(codes, 1):
        r = backfill_one(code, weeks=weeks)
        per_code[code] = r
        if r["rows_written"] > 0:
            stats["ok"] += 1
            stats["total_rows_written"] += r["rows_written"]
        else:
            reason = r.get("reason", "unknown")
            stats[reason] = stats.get(reason, 0) + 1
            missing.append(code)
            if reason == "fetch_error":
                stats["errors"].append({"code": code, "error": r.get("error", "")})
        if i % 20 == 0 or i == expected:
            logger.info(
                "[beneficial][backfill] progress %d/%d  ok=%d no_data=%d err=%d rows=%d",
                i, expected, stats["ok"], stats["no_data"], stats["fetch_error"],
                stats["total_rows_written"],
            )

    # 紀律 #20:結尾紀錄
    success = stats["fetch_error"] == 0  # fetch 全沒爆才算成功(no_data 是業務層,不算錯)
    err_msg = None
    if not success:
        err_msg = (f"{stats['fetch_error']} fetch error(s); "
                   f"first 3: {stats['errors'][:3]}")
    record_sync_attempt(
        source=SYNC_SOURCE,
        success=success,
        rows=stats["total_rows_written"],
        error=err_msg,
        missing=missing,
    )

    logger.info(
        "[beneficial][backfill] done: ok=%d / no_data=%d / no_total=%d / err=%d / rows=%d",
        stats["ok"], stats["no_data"], stats["no_total_rows"],
        stats["fetch_error"], stats["total_rows_written"],
    )

    stats["per_code"] = per_code
    return stats


def sync_all_latest() -> dict:
    """Cron 每週日呼叫:全 active ETF 各抓最新 2 週(覆蓋上週 + 補 anomaly)。

    跟 backfill_all 共用 per-ETF 邏輯,只差 weeks 預設小。
    """
    return backfill_all(weeks=2)
