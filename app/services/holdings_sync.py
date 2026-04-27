"""ETF 持股 + 持股變動爬蟲(CMoney 為主要資料源,2026-04-27 換)。

CMoney API 偵察結果(2026-04-27):
    URL:  GET https://www.cmoney.tw/api/cm/MobileService/ashx/GetDtnoData.ashx
    Query: action=getdtnodata & DtNo=59449513 & ParamStr=... & FilterNo=0
    ParamStr: AssignID=<code>;MTPeriod=0;DTMode=0;DTRange=10;DTOrder=1;MajorTable=M722;
    回:   {"Title": [...], "Data": [[date, code, name, weight%, shares, unit], ...]}
    一支 ETF ~520 rows = 10 個交易日 × ~52 持股
    無產業欄位(sector → NULL)
    無反爬機制(偵察 4 次都通)

落地:
- 紀律 #2 資料主權:只在排程 / CLI / admin 觸發,使用者頁面 100% 讀本地
- 紀律 #20 完整性:expected/actual/missing/errors → record_sync_attempt 持久化
- 紀律 #14 不 silent fail:任何 error log.warning + 記 errors list
- 寫雙表:
  · holdings:每個交易日的 snapshot 一個 batch(updated_at = 該日 datetime)
  · holdings_change:近 N 日 vs 最舊日,算 buy/sell/new,batch updated_at = now
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import select

from app.database import session_scope
from app.models.etf import ETF
from app.models.holdings import Holding
from app.models.holdings_change import HoldingsChange
from app.services.sync_status import record_sync_attempt

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# CMoney API 設定
# ──────────────────────────────────────────────────────────────────

CMONEY_URL = "https://www.cmoney.tw/api/cm/MobileService/ashx/GetDtnoData.ashx"
CMONEY_DTNO = "59449513"
CMONEY_DTRANGE = 10           # 抓近 10 個交易日

# Throttle:每支 ETF 之間的最少間隔(秒)
INTER_REQUEST_SLEEP = 0.5

# HTTP timeout
HTTP_TIMEOUT = 15.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SOURCE_TAG = "cmoney"


# ──────────────────────────────────────────────────────────────────
# Fetch + parse
# ──────────────────────────────────────────────────────────────────

def _build_url(etf_code: str) -> str:
    ps = (
        f"AssignID={etf_code};MTPeriod=0;DTMode=0;"
        f"DTRange={CMONEY_DTRANGE};DTOrder=1;MajorTable=M722;"
    )
    return (
        f"{CMONEY_URL}?action=getdtnodata&DtNo={CMONEY_DTNO}"
        f"&ParamStr={urllib.parse.quote(ps, safe='')}&FilterNo=0"
    )


def fetch_cmoney_one(etf_code: str) -> list[list]:
    """打 CMoney API,回原始 Data list。HTTP / format 失敗 raise。"""
    r = httpx.get(
        _build_url(etf_code),
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    payload = r.json()
    if "Error" in payload:
        raise RuntimeError(f"CMoney error: {payload['Error']}")
    rows = payload.get("Data") or []
    if not rows:
        raise RuntimeError("CMoney returned empty Data")
    return rows


def _parse_date_yyyymmdd(s: str) -> date:
    """'20260424' → date(2026, 4, 24)。"""
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _parse_snapshots(rows: list[list]) -> dict[date, list[dict]]:
    """切成「每日 snapshot」:{date: [{stock_code, stock_name, weight, shares, rank}, ...]}

    每日 rank 由 weight 排序(weight 大 → rank 1)。
    """
    by_day: dict[date, list[dict]] = defaultdict(list)
    for r in rows:
        if len(r) < 6:
            continue
        try:
            d = _parse_date_yyyymmdd(r[0])
            code = (r[1] or "").strip()
            name = (r[2] or "").strip()
            weight = float(r[3])
            shares = int(r[4])
        except (ValueError, TypeError, IndexError):
            continue
        if not code or not name:
            continue
        by_day[d].append({
            "stock_code": code,
            "stock_name": name,
            "weight": weight,
            "shares": shares,
        })

    # 每日內按 weight DESC 排序 + 給 rank
    for d, lst in by_day.items():
        lst.sort(key=lambda x: x["weight"], reverse=True)
        for i, item in enumerate(lst, start=1):
            item["rank"] = i
    return by_day


def _compute_changes(by_day: dict[date, list[dict]]) -> list[dict]:
    """比對最新日 vs 最舊日,算 buy/sell/new。

    Returns list of {stock_code, stock_name, change_direction,
                     shares_diff, weight_latest, latest_date, previous_date}
    """
    if len(by_day) < 2:
        return []

    dates = sorted(by_day.keys())
    oldest, latest = dates[0], dates[-1]
    old_map = {x["stock_code"]: x for x in by_day[oldest]}
    new_map = {x["stock_code"]: x for x in by_day[latest]}

    changes: list[dict] = []
    for code, new_h in new_map.items():
        old_h = old_map.get(code)
        old_shares = old_h["shares"] if old_h else 0
        diff = new_h["shares"] - old_shares
        if diff == 0:
            continue
        if old_shares == 0:
            direction = "new"
        elif diff > 0:
            direction = "buy"
        else:
            direction = "sell"
        changes.append({
            "stock_code": code,
            "stock_name": new_h["stock_name"],
            "change_direction": direction,
            "shares_diff": diff,
            "weight_latest": new_h["weight"],
            "latest_date": latest,
            "previous_date": oldest,
        })
    # 也補「賣到 0」的(only in old, gone in new)
    for code, old_h in old_map.items():
        if code in new_map:
            continue
        changes.append({
            "stock_code": code,
            "stock_name": old_h["stock_name"],
            "change_direction": "sell",
            "shares_diff": -old_h["shares"],
            "weight_latest": None,
            "latest_date": latest,
            "previous_date": oldest,
        })
    return changes


# ──────────────────────────────────────────────────────────────────
# Persist
# ──────────────────────────────────────────────────────────────────

def _persist_snapshots(etf_id: int, by_day: dict[date, list[dict]]) -> int:
    """每個交易日寫一個 batch(updated_at = 該日 00:00:00)。

    UNIQUE(etf_id, stock_code, updated_at)保證重跑不重複。
    """
    written = 0
    with session_scope() as s:
        for d, lst in by_day.items():
            batch_at = datetime.combine(d, datetime.min.time())
            # 該批已存在就跳過(idempotent)
            existing_count = s.scalar(
                select(Holding.id)
                .where(Holding.etf_id == etf_id)
                .where(Holding.updated_at == batch_at)
                .limit(1)
            )
            if existing_count:
                continue
            for item in lst[:10]:  # Top 10 only(plan)
                s.add(Holding(
                    etf_id=etf_id,
                    stock_code=item["stock_code"],
                    stock_name=item["stock_name"],
                    weight=item["weight"],
                    sector=None,
                    rank=item["rank"],
                    updated_at=batch_at,
                    source=SOURCE_TAG,
                ))
                written += 1
    return written


def _persist_changes(etf_id: int, changes: list[dict], batch_at: datetime) -> int:
    if not changes:
        return 0
    with session_scope() as s:
        # 同 batch 已寫過就 skip(idempotent)
        existing = s.scalar(
            select(HoldingsChange.id)
            .where(HoldingsChange.etf_id == etf_id)
            .where(HoldingsChange.updated_at == batch_at)
            .limit(1)
        )
        if existing:
            return 0
        for c in changes:
            s.add(HoldingsChange(
                etf_id=etf_id,
                stock_code=c["stock_code"],
                stock_name=c["stock_name"],
                change_direction=c["change_direction"],
                shares_diff=c["shares_diff"],
                weight_latest=c["weight_latest"],
                latest_date=c["latest_date"],
                previous_date=c["previous_date"],
                updated_at=batch_at,
                source=SOURCE_TAG,
            ))
    return len(changes)


# ──────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────

def sync_etf_holdings_cmoney(codes: list[str]) -> dict[str, Any]:
    """CMoney holdings + 變動 sync 主入口。

    紀律 #20:expected/actual/missing/errors → record_sync_attempt。
    """
    expected = list(codes)
    actual: list[str] = []
    errors: list[str] = []
    holdings_written = 0
    changes_written = 0
    batch_at = datetime.now()

    with session_scope() as s:
        etf_map = dict(
            s.execute(
                select(ETF.code, ETF.id).where(ETF.code.in_(expected))
            ).all()
        )

    for i, code in enumerate(expected, start=1):
        etf_id = etf_map.get(code)
        if etf_id is None:
            errors.append(f"{code}: not in etf_list")
            continue
        try:
            raw = fetch_cmoney_one(code)
            by_day = _parse_snapshots(raw)
            if not by_day:
                errors.append(f"{code}: 0 valid snapshot rows")
                continue
            holdings_written += _persist_snapshots(etf_id, by_day)
            changes = _compute_changes(by_day)
            changes_written += _persist_changes(etf_id, changes, batch_at)
            actual.append(code)
            logger.info(
                "[holdings] %s OK (snapshots=%d, changes=%d)",
                code, len(by_day), len(changes),
            )
        except Exception as e:
            errors.append(f"{code}: {type(e).__name__}: {e}")
            logger.warning("[holdings] %s FAIL — %s", code, e)
        time.sleep(INTER_REQUEST_SLEEP)

    missing = [c for c in expected if c not in actual]
    success = len(missing) == 0 and not errors

    record_sync_attempt(
        source="holdings_cmoney",
        success=success,
        rows=holdings_written + changes_written,
        error="; ".join(errors)[:1900] if errors else None,
        missing=missing,
    )

    result = {
        "source": SOURCE_TAG,
        "expected_etfs": expected,
        "actual_etfs": actual,
        "missing_etfs": missing,
        "holdings_written": holdings_written,
        "changes_written": changes_written,
        "errors": errors,
    }
    logger.info(
        "[holdings] done: expected=%d actual=%d missing=%d holdings=%d changes=%d errors=%d",
        len(expected), len(actual), len(missing),
        holdings_written, changes_written, len(errors),
    )
    return result


if __name__ == "__main__":
    """CLI 試水:python -m app.services.holdings_sync"""
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(levelname)-7s | %(name)s | %(message)s",
    )

    print("\n=== 試水:CMoney holdings 0050 / 0056 / 0051 ===\n")
    result = sync_etf_holdings_cmoney(["0050", "0056", "0051"])
    print("\n=== Result ===")
    for k, v in result.items():
        print(f"  {k}: {v}")

    from app.services.sync_status import get_sync_status
    ss = get_sync_status("holdings_cmoney")
    print(f"\nsync_status: {ss}")
