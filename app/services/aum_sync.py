"""ETF 規模(AUM)月歷史 — SITCA etf_statement2.aspx 月更。

Phase 0 PoC(2026-05-04)確認:SITCA chart iframe URL 給 286 ETF × 25 欄,
單 GET ~700KB,**不需要 Playwright**。

URL: https://www.sitca.org.tw/ROC/SITCA_ETF/etf_statement2.aspx
     ?txtYM=YYYYMM&txtR1=0

策略:
- 每月 1 GET 拿全市場 ~286 ETF
- 取「證券代號」+「基金規模(台幣)」兩欄
- raw NTD → round(/1000) 銀行家捨入 → INT 千元 → 存 etf_aum
- backfill_all_months(n=12) 跑 12 個月歷史
- sync_latest_month() cron 用,抓最新可拿的月份
- SITCA 月報延遲 1-2 個月,當月通常拿不到

紀律 #20:
- 該月 fetch_error → 不寫,該 ym 入 missing_items
- 該月 row < MIN_ETF_PER_MONTH(200) → 視為 SITCA 退化批,不寫
- 連續月 |Δ| > 50% → log warning(資料源權威,仍寫)

紀律 #18:銀行家捨入(Python 內建 round())金融資料約定,非 //
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Iterator

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import session_scope
from app.models.etf_aum import EtfAum
from app.services.sync_status import record_sync_attempt

logger = logging.getLogger(__name__)

SYNC_SOURCE = "sitca_aum_monthly"
SITCA_URL = "https://www.sitca.org.tw/ROC/SITCA_ETF/etf_statement2.aspx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ETFWatchBot/1.0)",
    "Referer": "https://www.sitca.org.tw/ROC/SITCA_ETF/etf_statement.aspx",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

# 異常閾值(連續月 |Δ| > 50% → log,仍寫)
ANOMALY_THRESHOLD = 0.50
# 退化閾值(該月 < N row → 視為 SITCA 沒釋出 / 退化批,不寫)
MIN_ETF_PER_MONTH = 200

DEFAULT_BACKFILL_MONTHS = 12
ETF_CODE_RE = re.compile(r"^\d{4,5}[A-Z]?$")


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _ym_iter_back(latest_ym: str, n: int) -> Iterator[str]:
    """從 latest_ym 往前數 n 個月(含 latest)— iterator yields 'YYYYMM'。"""
    y, m = int(latest_ym[:4]), int(latest_ym[4:6])
    for _ in range(n):
        yield f"{y:04d}{m:02d}"
        m -= 1
        if m == 0:
            y -= 1
            m = 12


def _latest_available_ym(today: date | None = None) -> str:
    """SITCA 月報延遲 1-2 月,從上個月開始嘗試(實際抓不到時往前走)。"""
    today = today or date.today()
    y, m = today.year, today.month - 1
    if m == 0:
        y -= 1
        m = 12
    return f"{y:04d}{m:02d}"


# ─────────────────────────────────────────────────────────────
# Fetch + parse
# ─────────────────────────────────────────────────────────────

def _fetch_month(ym: str, client: httpx.Client | None = None) -> list[dict]:
    """GET SITCA 該月資料,回 [{code, aum_thousand_ntd}, ...]。

    raise RuntimeError 若 HTTP 非 200 / 缺欄位。回空 list 若該月沒資料(尚未公布)。
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(headers=HEADERS, timeout=30)
    try:
        r = client.get(SITCA_URL, params={"txtYM": ym, "txtR1": "0"})
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        if not table:
            return []  # 月份太新還沒釋出 / SITCA 改版

        # 找關鍵欄 index(SITCA 用顯示文字 header)
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        idx_code = idx_aum = None
        for i, h in enumerate(headers):
            if "證券代號" in h:
                idx_code = i
            elif "基金規模(台幣)" in h:
                idx_aum = i
        if idx_code is None or idx_aum is None:
            raise RuntimeError(
                f"SITCA 表格欄位異動?missing code_idx={idx_code} aum_idx={idx_aum}"
            )

        out: list[dict] = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) <= max(idx_code, idx_aum):
                continue
            code = cells[idx_code]
            if not ETF_CODE_RE.match(code):
                continue  # 跳過 header / 非 ETF row
            aum_raw = cells[idx_aum].replace(",", "").strip()
            if not aum_raw or aum_raw == "0":
                continue  # 紀律 #20:不寫 0
            try:
                aum_ntd = int(aum_raw)
            except ValueError:
                logger.warning("[aum][%s] %s aum 解析失敗:%r", ym, code, cells[idx_aum])
                continue
            if aum_ntd <= 0:
                continue
            # 銀行家捨入(Python round() 是 half-to-even)— 金融約定
            aum_thousand = round(aum_ntd / 1000)
            out.append({
                "code": code,
                "aum_thousand_ntd": aum_thousand,
            })
        return out
    finally:
        if own_client:
            client.close()


# ─────────────────────────────────────────────────────────────
# Persist
# ─────────────────────────────────────────────────────────────

def _upsert_month(ym: str, rows: list[dict]) -> int:
    """UPSERT 一個月的 AUM 資料。
    回實際寫入(進 SQL)筆數。順帶 anomaly check(僅 log)。
    """
    if not rows:
        return 0
    month_date = date(int(ym[:4]), int(ym[4:6]), 1)

    # 先查上月作 anomaly check
    prev_y, prev_m = month_date.year, month_date.month - 1
    if prev_m == 0:
        prev_y -= 1
        prev_m = 12
    prev_month_date = date(prev_y, prev_m, 1)
    prev_aums: dict[str, int] = {}
    with session_scope() as s:
        prev_rows = s.execute(
            select(EtfAum.etf_code, EtfAum.aum_thousand_ntd)
            .where(EtfAum.month_date == prev_month_date)
        ).all()
        prev_aums = {r.etf_code: r.aum_thousand_ntd for r in prev_rows}

    payload = []
    for r in rows:
        code = r["code"]
        cur = r["aum_thousand_ntd"]
        prev = prev_aums.get(code)
        if prev and prev > 0:
            delta = abs(cur - prev) / prev
            if delta > ANOMALY_THRESHOLD:
                pct = (cur - prev) / prev * 100
                logger.warning(
                    "[aum][%s] %s anomaly ±%.1f%% (prev=%d → curr=%d 千元)",
                    ym, code, pct, prev, cur,
                )
        payload.append({
            "etf_code": code,
            "month_date": month_date,
            "aum_thousand_ntd": cur,
        })

    with session_scope() as s:
        for chunk_start in range(0, len(payload), 200):
            chunk = payload[chunk_start:chunk_start + 200]
            stmt = sqlite_insert(EtfAum).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["etf_code", "month_date"],
                set_={
                    "aum_thousand_ntd": stmt.excluded.aum_thousand_ntd,
                    "fetched_at": datetime.now(),
                },
            )
            s.execute(stmt)
    return len(payload)


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def backfill_all_months(months: int = DEFAULT_BACKFILL_MONTHS) -> dict:
    """從最新可拿的月開始往前 backfill N 個月。

    SITCA 月報延遲 1-2 月,當月幾乎拿不到 → 從上個月起。
    遇到沒資料的月(rows < MIN_ETF_PER_MONTH)記入 missing 但繼續往前。
    """
    latest_ym = _latest_available_ym()
    yms = list(_ym_iter_back(latest_ym, months))

    stats = {
        "target_months": months,
        "yms_attempted": yms,
        "ok": 0,                # 該月 row >= MIN
        "no_data": 0,           # 該月 SITCA 還沒釋出 / row 0
        "degraded": 0,          # row < MIN(視為退化)
        "fetch_error": 0,
        "total_rows_written": 0,
    }
    missing: list[str] = []

    logger.info("[aum][backfill] target months: %s", yms)
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        for ym in yms:
            try:
                rows = _fetch_month(ym, client=client)
            except Exception as e:
                logger.warning("[aum][%s] fetch error: %s", ym, e)
                stats["fetch_error"] += 1
                missing.append(ym)
                continue

            if not rows:
                logger.info("[aum][%s] no data (該月可能尚未釋出)", ym)
                stats["no_data"] += 1
                missing.append(ym)
                continue
            if len(rows) < MIN_ETF_PER_MONTH:
                logger.warning(
                    "[aum][%s] degraded: %d rows < %d threshold,不寫入",
                    ym, len(rows), MIN_ETF_PER_MONTH,
                )
                stats["degraded"] += 1
                missing.append(ym)
                continue

            n = _upsert_month(ym, rows)
            stats["ok"] += 1
            stats["total_rows_written"] += n
            logger.info("[aum][%s] wrote %d rows", ym, n)

    # 紀律 #20
    success = stats["fetch_error"] == 0
    err_msg = None
    if not success:
        err_msg = f"{stats['fetch_error']} month(s) fetch error"
    record_sync_attempt(
        source=SYNC_SOURCE,
        success=success,
        rows=stats["total_rows_written"],
        error=err_msg,
        missing=missing,
    )

    logger.info(
        "[aum][backfill] done: ok=%d / no_data=%d / degraded=%d / err=%d / rows=%d",
        stats["ok"], stats["no_data"], stats["degraded"],
        stats["fetch_error"], stats["total_rows_written"],
    )
    return stats


def sync_latest_month() -> dict:
    """Cron 每月 5 號:抓最新可拿的月(latest_available_ym,通常是上個月)。

    若該月 SITCA 還沒釋出(no_data)→ missing 記下,排程層 5 分鐘後 retry。
    """
    return backfill_all_months(months=1)
