"""ETF 主檔同步:從 FinMind 抓全市場 ETF + 大盤 TAIEX,寫進 etf_list。

不寫死清單(CLAUDE.md 規定),每天排程跑一次,新上市自動進來。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.database import session_scope
from app.models.etf import ETF
from app.services import etf_classifier, finmind
from app.services.sync_status import record_sync_attempt

SYNC_SOURCE = "etf_universe"

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TAIEX_CODE = "TAIEX"


def _upsert_etf(
    session: "Session",
    *,
    code: str,
    name: str,
    category: str,
    listed_date: str | None = None,
    is_active: bool = True,
) -> tuple[ETF, bool]:
    """有就更新、沒有就新增。回傳 (obj, is_new)。"""
    existing = session.scalar(select(ETF).where(ETF.code == code))
    if existing:
        existing.name = name
        existing.category = category
        if listed_date:
            existing.listed_date = listed_date
        existing.is_active = is_active
        return existing, False
    obj = ETF(
        code=code,
        name=name,
        category=category,
        listed_date=listed_date,
        is_active=is_active,
    )
    session.add(obj)
    return obj, True


def _ensure_taiex(session: "Session") -> None:
    """大盤 TAIEX 也存在 etf_list,分類 = index,排行榜不會抓它。"""
    _upsert_etf(
        session,
        code=TAIEX_CODE,
        name="台灣加權指數",
        category="index",
        is_active=True,
    )


def sync_universe() -> dict:
    """執行一次完整 ETF 主檔同步,回傳統計摘要。

    紀律 #20:expected = FinMind 篩選後 ETF 數,actual = 實際 upsert 成功,
    missing = upsert 失敗的 code → record_sync_attempt。fetch 整個爆例外
    也會 record(success=False, error=...)。
    """
    finmind.log_quota("before sync_universe")

    try:
        rows = finmind.request("TaiwanStockInfo")
    except Exception as e:
        logger.exception("[universe] FinMind fetch failed")
        record_sync_attempt(
            source=SYNC_SOURCE, success=False, rows=0,
            error=f"fetch: {type(e).__name__}: {str(e)[:200]}",
        )
        raise

    logger.info("[universe] FinMind returned %d rows", len(rows))

    # 過濾出 ETF(industry_category 中文「ETF」或「受益憑證」)
    etfs = []
    seen_codes: set[str] = set()
    for row in rows:
        ind = (row.get("industry_category") or "").strip()
        if ind not in {"ETF", "受益憑證"} and not ind.startswith("ETF"):
            continue
        code = (row.get("stock_id") or "").strip()
        name = (row.get("stock_name") or "").strip()
        if not code or not name or code in seen_codes:
            continue
        seen_codes.add(code)
        etfs.append((code, name, row.get("date")))

    logger.info("[universe] filtered to %d unique ETFs", len(etfs))

    expected_codes = [e[0] for e in etfs]
    actual_codes: list[str] = []
    errors: list[str] = []

    stats = {
        "total_in_feed": len(rows),
        "etf_count": len(etfs),
        "added": 0,
        "updated": 0,
        "by_category": {},
    }

    with session_scope() as session:
        _ensure_taiex(session)

        for code, name, listed_date in etfs:
            try:
                cat = etf_classifier.classify(code, name)
                _, is_new = _upsert_etf(
                    session,
                    code=code,
                    name=name,
                    category=cat,
                    listed_date=listed_date,
                )
                stats["added" if is_new else "updated"] += 1
                stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
                actual_codes.append(code)
            except Exception as e:
                errors.append(f"{code}: {type(e).__name__}: {str(e)[:80]}")
                logger.exception("[universe] upsert failed on %s", code)

    finmind.log_quota("after sync_universe")

    # 紀律 #20
    missing = [c for c in expected_codes if c not in actual_codes]
    success = len(missing) == 0 and not errors
    record_sync_attempt(
        source=SYNC_SOURCE,
        success=success,
        rows=stats["added"] + stats["updated"],
        error="; ".join(errors)[:1900] if errors else None,
        missing=missing,
    )
    stats["expected"] = len(expected_codes)
    stats["actual"] = len(actual_codes)
    stats["missing"] = missing

    logger.info(
        "[universe] sync done: added=%d updated=%d missing=%d by_category=%s",
        stats["added"], stats["updated"], len(missing), stats["by_category"],
    )
    return stats


def list_active_etfs(include_index: bool = True) -> list[ETF]:
    """回傳所有 is_active 的 ETF + 可選大盤。給 kbar_sync 用。"""
    with session_scope() as session:
        stmt = select(ETF).where(ETF.is_active.is_(True))
        if not include_index:
            stmt = stmt.where(ETF.category != "index")
        rows = session.scalars(stmt).all()
        # detach 一下避免 session 關掉後 lazy load 出問題
        for r in rows:
            session.expunge(r)
        return list(rows)
