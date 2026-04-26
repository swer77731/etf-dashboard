"""配息同步:從 FinMind TaiwanStockDividend 抓現金股利,寫進 dividend table。

策略跟 K 棒同步一致:
- 第一次:從 5 年前的 1/1 開始 backfill
- 之後:看 DB 該 ETF 最後一筆 ex_date,只補後面缺的
- TAIEX / 指數類:跳過(指數沒配息)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import session_scope
from app.models.dividend import Dividend
from app.models.etf import ETF
from app.services import finmind
from app.services.etf_universe import list_active_etfs

logger = logging.getLogger(__name__)

HISTORY_YEARS = 5


def _five_years_ago(today: date | None = None) -> date:
    today = today or date.today()
    return today.replace(year=today.year - HISTORY_YEARS, month=1, day=1)


def _last_ex_date(session, etf_id: int) -> date | None:
    return session.scalar(select(func.max(Dividend.ex_date)).where(Dividend.etf_id == etf_id))


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _persist_divs(etf_id: int, rows: list[dict]) -> int:
    """SQLite UPSERT — 同 (etf_id, ex_date) 已存在就更新。"""
    if not rows:
        return 0
    payload = []
    for r in rows:
        ex_date = _parse_date(r.get("CashExDividendTradingDate")) or _parse_date(r.get("StockExDividendTradingDate"))
        if not ex_date:
            continue
        cash = float(r.get("CashEarningsDistribution") or 0) + float(r.get("CashStatutorySurplus") or 0)
        stock = float(r.get("StockEarningsDistribution") or 0) + float(r.get("StockStatutorySurplus") or 0)
        if cash <= 0 and stock <= 0:
            continue
        payload.append({
            "etf_id": etf_id,
            "ex_date": ex_date,
            "cash_dividend": cash,
            "stock_dividend": stock,
            "payment_date": _parse_date(r.get("CashDividendPaymentDate")),
            "announce_date": _parse_date(r.get("AnnouncementDate")),
            "fiscal_year": str(r.get("year") or "")[:8] or None,
        })
    if not payload:
        return 0
    with session_scope() as session:
        for chunk_start in range(0, len(payload), 200):
            chunk = payload[chunk_start:chunk_start + 200]
            stmt = sqlite_insert(Dividend).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["etf_id", "ex_date"],
                set_={
                    "cash_dividend": stmt.excluded.cash_dividend,
                    "stock_dividend": stmt.excluded.stock_dividend,
                    "payment_date": stmt.excluded.payment_date,
                    "announce_date": stmt.excluded.announce_date,
                    "fiscal_year": stmt.excluded.fiscal_year,
                },
            )
            session.execute(stmt)
    return len(payload)


def sync_one_etf(etf: ETF, end: date | None = None) -> dict:
    end = end or date.today()
    if etf.category == "index":
        return {"code": etf.code, "rows": 0, "mode": "skip-index"}

    with session_scope() as session:
        last = _last_ex_date(session, etf.id)

    if last is None:
        start = _five_years_ago(end)
        mode = "backfill"
    else:
        start = last + timedelta(days=1)
        mode = "incremental"
        if start > end:
            return {"code": etf.code, "rows": 0, "mode": "up-to-date"}

    rows = finmind.request(
        "TaiwanStockDividend",
        data_id=etf.code,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
    )
    written = _persist_divs(etf.id, rows)
    return {"code": etf.code, "rows": written, "mode": mode,
            "range": (start.isoformat(), end.isoformat())}


def sync_all(etfs: Iterable[ETF] | None = None, end: date | None = None) -> dict:
    end = end or date.today()
    finmind.log_quota("before dividend sync_all")

    targets = list(etfs) if etfs is not None else list_active_etfs(include_index=False)
    logger.info("[dividend_sync] start: %d ETFs, target end=%s", len(targets), end)

    summary = {"total": len(targets), "ok": 0, "empty": 0, "error": 0, "rows_written": 0}
    for i, etf in enumerate(targets, start=1):
        try:
            res = sync_one_etf(etf, end=end)
            summary["rows_written"] += res["rows"]
            if res["rows"] > 0:
                summary["ok"] += 1
            else:
                summary["empty"] += 1
            if i % 25 == 0 or i == len(targets):
                q = finmind.check_quota()
                logger.info(
                    "[dividend_sync] progress %d/%d | %s rows=%d mode=%s | quota=%d/%d (%.1f%%)",
                    i, len(targets), res["code"], res["rows"], res["mode"],
                    q.used, q.limit_hour, q.ratio * 100,
                )
        except Exception as e:
            summary["error"] += 1
            logger.exception("[dividend_sync] failed on %s: %s", etf.code, e)

    finmind.log_quota("after dividend sync_all")
    logger.info("[dividend_sync] done: %s", summary)
    return summary
