"""K 棒同步:每支 ETF 抓原始價 + 還原價,寫進 daily_kbar。

策略:
- 第一次:從 5 年前的 1/1 開始 backfill
- 之後:看 DB 該 ETF 最後一筆是哪天,只補後面缺的
- TAIEX:沒有還原價,只抓 TaiwanStockPrice
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import session_scope
from app.models.etf import ETF
from app.models.kbar import DailyKBar
from app.services import finmind
from app.services.etf_universe import TAIEX_CODE, list_active_etfs
from app.services.sync_status import record_sync_attempt

SYNC_SOURCE = "kbar_sync"

logger = logging.getLogger(__name__)

HISTORY_YEARS = 5


def _five_years_ago(today: date | None = None) -> date:
    today = today or date.today()
    return today.replace(year=today.year - HISTORY_YEARS, month=1, day=1)


def _last_kbar_date(session, etf_id: int) -> date | None:
    return session.scalar(select(func.max(DailyKBar.date)).where(DailyKBar.etf_id == etf_id))


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _fetch_raw(code: str, start: date, end: date) -> list[dict]:
    return finmind.request(
        "TaiwanStockPrice",
        data_id=code,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
    )


def _fetch_adj(code: str, start: date, end: date) -> list[dict]:
    return finmind.request(
        "TaiwanStockPriceAdj",
        data_id=code,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
    )


def _merge_raw_adj(raw_rows: list[dict], adj_rows: list[dict]) -> list[dict]:
    """以 raw 為主軸,把 adj 的 close/open/high/low 對應日期合進去。"""
    adj_by_date = {r["date"]: r for r in adj_rows}
    merged: list[dict] = []
    for r in raw_rows:
        d = r["date"]
        a = adj_by_date.get(d, {})
        merged.append({
            "date": d,
            "open": float(r.get("open") or 0),
            "high": float(r.get("max") or 0),
            "low": float(r.get("min") or 0),
            "close": float(r.get("close") or 0),
            "volume": int(r.get("Trading_Volume") or 0),
            "adj_open": float(a["open"]) if a.get("open") not in (None, "") else None,
            "adj_high": float(a["max"]) if a.get("max") not in (None, "") else None,
            "adj_low": float(a["min"]) if a.get("min") not in (None, "") else None,
            "adj_close": float(a["close"]) if a.get("close") not in (None, "") else None,
        })
    return merged


def _persist_kbars(etf_id: int, rows: list[dict]) -> int:
    """SQLite UPSERT:同 (etf_id, date) 已存在就更新。回傳寫入筆數。"""
    if not rows:
        return 0
    with session_scope() as session:
        for chunk_start in range(0, len(rows), 500):
            chunk = rows[chunk_start:chunk_start + 500]
            stmt = sqlite_insert(DailyKBar).values([
                {
                    "etf_id": etf_id,
                    "date": _parse_date(r["date"]),
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "volume": r["volume"],
                    "adj_open": r["adj_open"],
                    "adj_high": r["adj_high"],
                    "adj_low": r["adj_low"],
                    "adj_close": r["adj_close"],
                }
                for r in chunk
            ])
            stmt = stmt.on_conflict_do_update(
                index_elements=["etf_id", "date"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "adj_open": stmt.excluded.adj_open,
                    "adj_high": stmt.excluded.adj_high,
                    "adj_low": stmt.excluded.adj_low,
                    "adj_close": stmt.excluded.adj_close,
                },
            )
            session.execute(stmt)
    return len(rows)


def sync_one_etf(etf: ETF, end: date | None = None) -> dict:
    """同步單一 ETF。回傳 {code, range, rows, mode}。"""
    end = end or date.today()
    is_index = etf.category == "index"

    with session_scope() as session:
        last = _last_kbar_date(session, etf.id)

    if last is None:
        start = _five_years_ago(end)
        mode = "backfill"
    else:
        start = last + timedelta(days=1)
        mode = "incremental"
        if start > end:
            return {"code": etf.code, "rows": 0, "mode": "up-to-date", "range": None}

    raw = _fetch_raw(etf.code, start, end)
    adj = [] if is_index else _fetch_adj(etf.code, start, end)
    merged = _merge_raw_adj(raw, adj)
    written = _persist_kbars(etf.id, merged)

    return {
        "code": etf.code,
        "rows": written,
        "mode": mode,
        "range": (start.isoformat(), end.isoformat()),
    }


def sync_all(etfs: Iterable[ETF] | None = None, end: date | None = None) -> dict:
    """同步全市場(或給定子集)。會主動 log quota 與進度。

    紀律 #20:expected = active ETF 清單,actual = 沒爆例外的 ETF,
    missing = exception 的 ETF code 清單 → record_sync_attempt 持久化。
    """
    end = end or date.today()
    finmind.log_quota("before sync_all")

    targets = list(etfs) if etfs is not None else list_active_etfs(include_index=True)
    expected_codes = [e.code for e in targets]
    actual_codes: list[str] = []
    errors: list[str] = []
    logger.info("[kbar_sync] start: %d ETFs, target end=%s", len(targets), end)

    summary = {"total": len(targets), "ok": 0, "empty": 0, "error": 0, "rows_written": 0}
    for i, etf in enumerate(targets, start=1):
        try:
            res = sync_one_etf(etf, end=end)
            summary["rows_written"] += res["rows"]
            if res["rows"] > 0:
                summary["ok"] += 1
            else:
                summary["empty"] += 1
            actual_codes.append(etf.code)
            if i % 10 == 0 or i == len(targets):
                q = finmind.check_quota()
                logger.info(
                    "[kbar_sync] progress %d/%d | last=%s rows=%d mode=%s | quota=%d/%d (%.1f%%)",
                    i, len(targets), res["code"], res["rows"], res["mode"],
                    q.used, q.limit_hour, q.ratio * 100,
                )
        except Exception as e:
            summary["error"] += 1
            errors.append(f"{etf.code}: {type(e).__name__}: {str(e)[:80]}")
            logger.exception("[kbar_sync] failed on %s: %s", etf.code, e)

    finmind.log_quota("after sync_all")

    # 紀律 #20:expected/actual/missing → sync_status 持久化
    missing = [c for c in expected_codes if c not in actual_codes]
    success = len(missing) == 0 and not errors
    record_sync_attempt(
        source=SYNC_SOURCE,
        success=success,
        rows=summary["rows_written"],
        error="; ".join(errors)[:1900] if errors else None,
        missing=missing,
    )
    summary["expected"] = len(expected_codes)
    summary["actual"] = len(actual_codes)
    summary["missing"] = missing

    logger.info("[kbar_sync] done: %s", summary)
    return summary
