"""TWSE 除權除息預告表爬蟲(Phase 1B-2)

資料源:TWSE TWT48U 除權除息預告表
URL: https://www.twse.com.tw/exchangeReport/TWT48U?response=json&strDate=...&endDate=...

【目前範圍】
本 Step 只做 fetch + parse + 過期過濾,**不接 DB、不接 scheduler、不碰 sync_status**。
獨立可跑:`python -m app.services.dividend_announce_sync`

【落地紀律】
- 「現金股利」HTML(待公告)時 → `cash_dividend=None`(UI 顯示「待公告」灰字,
  **不要顯示 0 或空白** — 用戶要的就是「知道幾號除息,金額還在等」)
- 過期過濾:`ex_date >= today` 才回(注意 `>=` 不是 `>`,**今天除息也要保留**)
- 日期 parse 雙重驗證:索引 0 民國年(主)+ 索引 8 西元(cross-check),
  不一致 → log warning + 跳過該筆
- announce_date / payment_date TWSE 沒給,**留給後續 dividend_sync(FinMind)補**
- 純股票股利("權")→ skip(我們關心 cash)

【UPSERT 規則(實作 persist 時必須遵守)】
歷史 sync 已有的非 NULL 欄位(例如 fiscal_year=2024)
**不能被新進的 NULL 蓋掉**。
未來寫 persist() 時,只更新明確有值的欄位,NULL 不覆寫。
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

import httpx

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


if __name__ == "__main__":
    """獨立執行驗證 — 印出前 X 筆 raw + filtered 結果。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-7s | %(name)s | %(message)s",
    )

    today = date.today()
    end = today + timedelta(days=120)

    print(f"\n=== TWSE TWT48U Fetch ===")
    print(f"窗口:{today}  →  {end}\n")

    try:
        rows = fetch_twse(today, end, today=today)
    except Exception as e:
        print(f"❌ FAILED: {type(e).__name__}: {e}")
        raise

    print(f"\n=== 解析結果({len(rows)} 筆 ex_date >= today)===\n")
    print(f"{'代號':<8} {'除息日':<12} {'現金股利':<12} 名稱")
    print("-" * 60)
    for r in sorted(rows, key=lambda x: (x["ex_date"], x["code"])):
        cash_s = f"{r['cash_dividend']:.4f}" if r["cash_dividend"] is not None else "待公告"
        print(f"{r['code']:<8} {str(r['ex_date']):<12} {cash_s:<12} {r['name_from_source']}")

    # 統計
    has_cash = sum(1 for r in rows if r["cash_dividend"] is not None)
    print(f"\n統計:已公告金額 {has_cash} / 待公告 {len(rows) - has_cash}")
