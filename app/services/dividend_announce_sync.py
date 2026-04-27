"""TWSE 除權除息預告表爬蟲(Phase 1B-2)

資料源:TWSE TWT48U 除權除息預告表
URL: https://www.twse.com.tw/exchangeReport/TWT48U?response=json&strDate=...&endDate=...

【目前範圍】
- fetch_twse: 抓 + parse + 過期過濾(不接 DB)
- persist:   etf_list 過濾 + UPSERT dividend table + sync_status 紀錄

獨立可跑:`python -m app.services.dividend_announce_sync`

【落地紀律】
- 「現金股利」HTML(待公告)時 → `cash_dividend=None`(UI 顯示「待公告」灰字,
  **不要顯示 0 或空白** — 用戶要的就是「知道幾號除息,金額還在等」)
- 過期過濾:`ex_date >= today` 才回(注意 `>=` 不是 `>`,**今天除息也要保留**)
- 日期 parse 雙重驗證:索引 0 民國年(主)+ 索引 8 西元(cross-check),
  不一致 → log warning + 跳過該筆
- announce_date / payment_date TWSE 沒給,**留給後續 dividend_sync(FinMind)補**
- 純股票股利("權")→ skip(我們關心 cash)

【UPSERT 鐵律(已實作於 persist)】
- cash_dividend = None **不寫入該欄**(保留既有值,避免歷史值被未公告值蓋掉)
- announce_date / payment_date / fiscal_year **永遠以非 NULL 為優先**(同上)
- 個股 / REIT / 不在 etf_list 的代號 → skip 不寫入(REIT 已在 fetch 層擋,
  個股在 persist 層用 etf_list lookup 擋)
- 實作:`COALESCE(excluded.col, dividend.col)` — 新值非 NULL 才覆蓋
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import session_scope
from app.models.dividend import Dividend
from app.models.etf import ETF
from app.services.sync_status import record_sync_attempt

logger = logging.getLogger(__name__)

TWSE_TWT48U_URL = "https://www.twse.com.tw/exchangeReport/TWT48U"

# 民國年中文格式 → "115年04月23日"
_ROC_DATE_RE = re.compile(r"^\s*(\d+)年(\d+)月(\d+)日\s*$")
# 索引 8 「詳細資料」欄第二段 西元 YYYYMMDD
_AD_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
# 索引 7 純數字 "1.00000000"
_FLOAT_RE = re.compile(r"^-?\d+(\.\d+)?$")

# fields 索引(2026-04-27 確認過 schema,共 13 欄)
IDX_EX_DATE_ROC = 0   # 除權除息日期
IDX_CODE        = 1   # 股票代號
IDX_NAME        = 2   # 名稱
IDX_EX_TYPE     = 3   # 除權息("權"/"息"/"權息")
IDX_CASH_DIV    = 7   # 現金股利
IDX_DETAIL      = 8   # 詳細資料(內含西元日期 cross-check)


def _parse_roc_date(s: str) -> date | None:
    """民國年「YYY年MM月DD日」→ date。"""
    if not s:
        return None
    m = _ROC_DATE_RE.match(s)
    if not m:
        return None
    roc_y, mo, d = (int(g) for g in m.groups())
    try:
        return date(roc_y + 1911, mo, d)
    except ValueError:
        return None


def _parse_ad_date_from_detail(s: str) -> date | None:
    """索引 8 「詳細資料」欄 = "<code>,<YYYYMMDD>",回西元日期。"""
    if not s:
        return None
    parts = s.strip().split(",")
    if len(parts) < 2:
        return None
    m = _AD_DATE_RE.match(parts[1].strip())
    if not m:
        return None
    try:
        return date(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def _parse_cash_dividend(s: str) -> float | None:
    """索引 7 現金股利。

    - "1.00000000" → 1.0
    - "<p>待公告實際收益分配金額</p>" → None
    - 任何解析失敗 / 0 / 負數 → None
    """
    s = (s or "").strip()
    if not _FLOAT_RE.match(s):
        return None
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def fetch_twse(start: date, end: date,
               today: date | None = None,
               timeout: float = 10.0) -> list[dict[str, Any]]:
    """從 TWSE TWT48U 拉除權除息預告 → parse → 過期過濾。

    Args:
        start / end: HTTP 抓取窗口(送給 TWSE 的 strDate / endDate)
        today: 過期 filter 基準(預設 `date.today()`)
        timeout: HTTP timeout 秒

    Returns:
        list of dict,每筆:
        {
            'code': '0050',
            'ex_date': date(2026, 5, 1),
            'cash_dividend': 1.0,             # None = 待公告
            'name_from_source': '元大台灣50',
            'source': 'twse_announce',
        }

    過濾(對照 CLAUDE.md 紀律 #12「ETF 觀察室收錄範圍」):
    - 「除權息」= "權"(純股票股利) → skip
    - 民國年 vs 西元 不一致 → log warning + skip
    - ex_date < today → skip(>= today 才保留)
    - **REIT(`01xxxT` 開頭 + T 結尾)→ skip**(紀律 #12)
    - 個股 / 權證 → 通過(讓 persist 層用 etf_list 過濾,爬蟲不做業務判斷)
    """
    today = today or date.today()
    params = {
        "response": "json",
        "strDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (ETF-Watch-dividend-announce-sync)",
        "Accept": "application/json, */*",
    }

    with httpx.Client(timeout=timeout, headers=headers) as client:
        resp = client.get(TWSE_TWT48U_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    if data.get("stat") != "OK":
        raise RuntimeError(f"TWSE TWT48U non-OK stat: {data.get('stat')!r}")

    rows = data.get("data") or []
    out: list[dict[str, Any]] = []
    skipped = {"pure_stock": 0, "date_mismatch": 0, "past": 0,
               "bad_row": 0, "no_code": 0, "bad_date": 0, "reit": 0}

    for r in rows:
        if not isinstance(r, list) or len(r) <= IDX_DETAIL:
            skipped["bad_row"] += 1
            logger.warning("[twse] row too short / wrong type: %s", r)
            continue

        # 純股票股利("權")— 我們關心 cash,跳過
        ex_type = (r[IDX_EX_TYPE] or "").strip()
        if ex_type == "權":
            skipped["pure_stock"] += 1
            continue

        # 雙重驗證日期:民國年 vs 西元
        roc_date = _parse_roc_date(r[IDX_EX_DATE_ROC] or "")
        ad_date = _parse_ad_date_from_detail(r[IDX_DETAIL] or "")

        if not roc_date:
            skipped["bad_date"] += 1
            logger.warning("[twse] cannot parse ROC date %r (code=%s)",
                           r[IDX_EX_DATE_ROC], r[IDX_CODE] if len(r) > IDX_CODE else "?")
            continue

        if ad_date and ad_date != roc_date:
            skipped["date_mismatch"] += 1
            logger.warning(
                "[twse] date mismatch — ROC %r → %s but detail says %s (code=%s)",
                r[IDX_EX_DATE_ROC], roc_date, ad_date,
                r[IDX_CODE] if len(r) > IDX_CODE else "?",
            )
            continue

        # 過期過濾:>= today(注意,今天除息也要保留 — user 最想看的 row)
        if roc_date < today:
            skipped["past"] += 1
            continue

        code = (r[IDX_CODE] or "").strip()
        if not code:
            skipped["no_code"] += 1
            continue

        # 紀律 #12:REIT(01xxxT)不收
        if code.upper().startswith("01") and code.upper().endswith("T"):
            skipped["reit"] += 1
            continue

        out.append({
            "code": code,
            "ex_date": roc_date,
            "cash_dividend": _parse_cash_dividend(r[IDX_CASH_DIV] or ""),
            "name_from_source": (r[IDX_NAME] or "").strip(),
            "source": "twse_announce",
        })

    logger.info(
        "[twse] window=%s~%s | fetched=%d kept=%d skipped=%s",
        start, end, len(rows), len(out), skipped,
    )
    return out


SYNC_SOURCE = "twse_dividend_announce"

# 排程抓取窗口:今天 ~ 今天 + 120 天
# 半年配的公告通常在 ex 前 30~60 天出,留 120 天緩衝
LOOKAHEAD_DAYS = 120


def sync_all(today: date | None = None) -> dict[str, Any]:
    """Step 4 排程入口:fetch + persist 一氣呵成。

    被 app.scheduler.daily_sync_job 呼叫(每天 14:30)。
    fetch 失敗也會 record_sync_attempt(failure)讓監控看得到。
    """
    today = today or date.today()
    end = today + timedelta(days=LOOKAHEAD_DAYS)

    try:
        rows = fetch_twse(today, end, today=today)
    except Exception as e:
        logger.exception("[announce_sync] fetch_twse failed")
        record_sync_attempt(
            SYNC_SOURCE, success=False, rows=0,
            error=f"fetch_twse: {type(e).__name__}: {e}",
        )
        return {
            "kept": 0,
            "skipped_not_etf": 0,
            "errors": [f"fetch_twse: {e}"],
            "stage": "fetch",
        }

    stats = persist(rows)
    stats["stage"] = "ok" if not stats["errors"] else "persist"
    return stats


def persist(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """把 fetch_twse 結果寫入 dividend table。

    - 對 etf_list 做 lookup,個股 / REIT / 不在清單的全 skip
    - UPSERT(on_conflict_do_update),只更新 cash_dividend
      (其他欄位 announce_date / payment_date / stock_dividend / fiscal_year
       TWSE 預告沒給,不在 INSERT values 也不在 on_conflict set_,既有值保留)
    - cash_dividend 走 COALESCE:新值非 NULL 才覆蓋,NULL 不蓋既有非 NULL
    - 結尾呼叫 record_sync_attempt(SYNC_SOURCE, ...)

    Returns:
        {'kept': int, 'skipped_not_etf': int, 'errors': list[str]}
    """
    stats: dict[str, Any] = {"kept": 0, "skipped_not_etf": 0, "errors": []}

    if not rows:
        record_sync_attempt(SYNC_SOURCE, success=True, rows=0)
        return stats

    # 1. etf_list 過濾(個股 / 不在清單的 code 砍掉)
    codes_in_rows = list({r["code"] for r in rows})
    with session_scope() as s:
        etf_map: dict[str, int] = dict(
            s.execute(
                select(ETF.code, ETF.id).where(ETF.code.in_(codes_in_rows))
            ).all()
        )

    payload: list[dict[str, Any]] = []
    for r in rows:
        etf_id = etf_map.get(r["code"])
        if etf_id is None:
            stats["skipped_not_etf"] += 1
            continue
        payload.append({
            "etf_id": etf_id,
            "ex_date": r["ex_date"],
            "cash_dividend": r["cash_dividend"],   # 可能是 None(待公告)
        })

    # 2. UPSERT — COALESCE 保護既有非 NULL
    if payload:
        try:
            with session_scope() as s:
                for chunk_start in range(0, len(payload), 200):
                    chunk = payload[chunk_start:chunk_start + 200]
                    stmt = sqlite_insert(Dividend).values(chunk)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["etf_id", "ex_date"],
                        set_={
                            # 鐵律:新值 NULL 時不蓋掉既有值
                            "cash_dividend": func.coalesce(
                                stmt.excluded.cash_dividend,
                                Dividend.cash_dividend,
                            ),
                        },
                    )
                    s.execute(stmt)
            stats["kept"] = len(payload)
        except Exception as e:
            logger.exception("[persist] UPSERT failed")
            stats["errors"].append(f"{type(e).__name__}: {e}")

    # 3. sync_status 紀錄
    success = not stats["errors"]
    record_sync_attempt(
        SYNC_SOURCE,
        success=success,
        rows=stats["kept"],
        error="; ".join(stats["errors"]) if stats["errors"] else None,
    )

    logger.info(
        "[persist] kept=%d skipped_not_etf=%d errors=%d",
        stats["kept"], stats["skipped_not_etf"], len(stats["errors"]),
    )
    return stats


if __name__ == "__main__":
    """獨立執行:fetch + persist + 驗收。

    驗收範圍(紀律 #14:不加碼):
    - dividend table 有 6 支 ETF 的未來除息日
    - sync_status 有 'twse_dividend_announce' 一筆,rows_synced=6
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-7s | %(name)s | %(message)s",
    )

    from app.models.sync_status import SyncStatus

    today = date.today()
    end = today + timedelta(days=120)

    print(f"\n=== fetch_twse({today} ~ {end}) ===")
    rows = fetch_twse(today, end, today=today)
    print(f"fetched {len(rows)} rows")

    print(f"\n=== persist ===")
    stats = persist(rows)
    print(f"stats: {stats}")

    # 驗收 1:dividend table 未來除息日的 ETF 筆數
    print(f"\n=== verify ===")
    with session_scope() as s:
        future = s.execute(
            select(Dividend.ex_date, ETF.code, Dividend.cash_dividend)
            .join(ETF, Dividend.etf_id == ETF.id)
            .where(Dividend.ex_date >= today)
            .order_by(Dividend.ex_date.asc(), ETF.code.asc())
        ).all()
        print(f"dividend rows with ex_date >= today: {len(future)}")
        for ex, code, cash in future:
            cash_s = f"{cash:.4f}" if cash is not None else "NULL"
            print(f"  {ex}  {code:<8} cash={cash_s}")

        # 驗收 2:sync_status 有紀錄
        ss = s.scalar(select(SyncStatus).where(SyncStatus.source == SYNC_SOURCE))
        if ss:
            print(f"\nsync_status: source={ss.source} success_at={ss.last_success_at} "
                  f"rows={ss.rows_synced} error={ss.last_error!r}")
        else:
            print("\nsync_status: NOT FOUND (FAIL)")
