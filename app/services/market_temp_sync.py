"""市場溫度計 5 個資料源 sync 服務。

對應 5 個 ORM models(C1 已建):
- margin_maintenance(融資維持率,XQ 含 ETF 口徑)
- market_breadth(漲跌家數,TWSE MI_INDEX 純股票)
- margin_short_total(融資+融券大盤合計)
- securities_lending_daily(借券當日交易)
- institutional_daily(三大法人寬表)

時間差紀律(各 sync 釋出時間不同):
- 14:35  breadth(收盤即釋出)
- 16:05  institutional(現貨 ~16:00 / 期貨選擇權 ~15:30)
- 17:35  lending(借券 ~17:30)
- 18:05  margin_short + maintenance(融資融券 ~18:00)
- 23:30  audit(全資料完整性檢查 + retry)

紀律 #20:每個 sync 都 record_sync_attempt(success, rows, missing_items)。
紀律 #22:audit 端定期掃今日 row 是否存在,缺漏進「人工待辦」。
紀律 #18:_redact 過濾 token / 不擅自 logging FinMind URL。
"""
from __future__ import annotations

import logging
import re as _re
from datetime import date as date_type, datetime, timedelta
from collections import defaultdict
from typing import Iterable

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert

from app.database import session_scope
from app.models.market_temperature import (
    MarginMaintenance,
    MarketBreadth,
    MarginShortTotal,
    SecuritiesLendingDaily,
    InstitutionalDaily,
)
from app.models.etf import ETF
from app.services import finmind
from app.services.sync_status import record_sync_attempt

logger = logging.getLogger(__name__)

_SECRET_RE = _re.compile(r"(token|password|api[_-]?key|secret|authorization)=([^&\s]+)", _re.IGNORECASE)


def _redact(s: str) -> str:
    return _SECRET_RE.sub(r"\1=***", str(s) or "")


def _today_tw() -> date_type:
    """Asia/Taipei 今日(UTC+8)。"""
    return (datetime.utcnow() + timedelta(hours=8)).date()


def _twse_stock_codes() -> set[str]:
    """快取一份 twse-only 上市 ETF + 股票代碼(從 etf_list,只用 ETF 代碼當 filter)。

    注意:這只是粗略 filter,實際 FinMind TaiwanStockInfo type='twse' 涵蓋
    所有上市標的。生產用 FinMind 直接撈避免依賴本地 ETF list。
    """
    info = finmind.request("TaiwanStockInfo")
    return {r["stock_id"] for r in info if r.get("type") == "twse"}


# ─────────────────────────────────────────────────────────────
# 1. 漲跌家數(TWSE MI_INDEX,純股票口徑)
# ─────────────────────────────────────────────────────────────
def sync_breadth(target: date_type | None = None) -> dict:
    """每日 1 row:date / up / down / flat。"""
    d = target or _today_tw()
    yyyymmdd = d.strftime("%Y%m%d")

    rows_written = 0
    missing: list[str] = []
    err_msg: str | None = None

    try:
        r = httpx.get(
            f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={yyyymmdd}&type=MS&response=json",
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"TWSE HTTP {r.status_code}")
        j = r.json()
        up, down, flat = None, None, None
        for t in j.get("tables", []):
            if "漲跌證券數" not in t.get("title", ""):
                continue
            for row in t.get("data", []):
                label = row[0] if row else ""
                stock_val = row[2] if len(row) >= 3 else "0"
                num = int(stock_val.split("(")[0].replace(",", ""))
                if "上漲" in label:
                    up = num
                elif "下跌" in label:
                    down = num
                elif "持平" in label:
                    flat = num
            break

        if up is None or down is None or flat is None:
            missing.append(d.isoformat())
            raise RuntimeError(f"TWSE 無漲跌證券數表(可能假日 / 未釋出)")

        with session_scope() as session:
            stmt = sqlite_upsert(MarketBreadth).values(
                date=d, up_count=up, down_count=down, flat_count=flat,
            ).on_conflict_do_update(
                index_elements=["date"],
                set_={"up_count": up, "down_count": down, "flat_count": flat},
            )
            session.execute(stmt)
        rows_written = 1
        logger.info("[market_temp.breadth] %s: 漲 %d / 跌 %d / 平 %d", d, up, down, flat)

    except Exception as e:
        err_msg = _redact(str(e))[:300]
        if not missing:
            missing.append(d.isoformat())
        logger.warning("[market_temp.breadth] %s failed: %s", d, err_msg)

    record_sync_attempt(
        source="mt_breadth",
        success=(not err_msg),
        rows=rows_written,
        error=err_msg,
        missing=missing,
    )
    return {"date": d.isoformat(), "rows": rows_written, "missing": missing, "error": err_msg}


# ─────────────────────────────────────────────────────────────
# 2. 三大法人(現貨 + 期貨 + 選擇權 合一寬表)
# ─────────────────────────────────────────────────────────────
_INST_MAP = {"外資": "foreign", "投信": "trust", "自營商": "dealer"}


def sync_institutional(target: date_type | None = None) -> dict:
    """每日 3 row(foreign/trust/dealer)。

    從 3 個 FinMind dataset 拿:
    - TaiwanStockTotalInstitutionalInvestors(現貨)
    - TaiwanFuturesInstitutionalInvestors(TX 大臺期貨)
    - TaiwanOptionInstitutionalInvestors(TXO 選擇權)
    """
    d = target or _today_tw()
    rows_written = 0
    missing: list[str] = []
    err_msg: str | None = None

    try:
        # 現貨
        spot = finmind.request("TaiwanStockTotalInstitutionalInvestors",
                               start_date=d.isoformat(), end_date=d.isoformat())
        spot_net: dict[str, float] = defaultdict(float)
        for r in spot:
            n = r.get("name") or ""
            net_yi = (float(r["buy"]) - float(r["sell"])) / 1e8
            if "Foreign" in n:
                spot_net["foreign"] += net_yi
            elif "Trust" in n or "Investment_Trust" in n:
                spot_net["trust"] += net_yi
            elif "Dealer" in n:
                spot_net["dealer"] += net_yi

        # 期貨 TX
        fut = finmind.request("TaiwanFuturesInstitutionalInvestors",
                              start_date=d.isoformat(), end_date=d.isoformat())
        fut_oi: dict[str, dict[str, int]] = defaultdict(lambda: {"long": 0, "short": 0})
        for r in fut:
            if r.get("futures_id") != "TX":
                continue
            who = _INST_MAP.get(r.get("institutional_investors"))
            if not who:
                continue
            fut_oi[who]["long"] = int(r.get("long_open_interest_balance_volume") or 0)
            fut_oi[who]["short"] = int(r.get("short_open_interest_balance_volume") or 0)

        # 選擇權 TXO(全 3 法人都收,但只外資存 4 項細部)
        opt = finmind.request("TaiwanOptionInstitutionalInvestors",
                              start_date=d.isoformat(), end_date=d.isoformat())
        opt_amt: dict[str, dict[str, float]] = defaultdict(
            lambda: {"buy_call": 0.0, "sell_call": 0.0, "buy_put": 0.0, "sell_put": 0.0}
        )
        for r in opt:
            if r.get("option_id") != "TXO":
                continue
            who = _INST_MAP.get(r.get("institutional_investors"))
            if not who:
                continue
            cp = r.get("call_put")
            long_amt = float(r.get("long_open_interest_balance_amount") or 0) / 1e5  # 億元
            short_amt = float(r.get("short_open_interest_balance_amount") or 0) / 1e5
            if cp == "買權":
                opt_amt[who]["buy_call"] += long_amt
                opt_amt[who]["sell_call"] += short_amt
            elif cp == "賣權":
                opt_amt[who]["buy_put"] += long_amt
                opt_amt[who]["sell_put"] += short_amt

        # UPSERT 3 row(foreign/trust/dealer)
        with session_scope() as session:
            for who in ("foreign", "trust", "dealer"):
                values = {
                    "date": d,
                    "institution": who,
                    "spot_net_yi": round(spot_net.get(who, 0.0), 2),
                    "fut_long_vol": fut_oi[who]["long"],
                    "fut_short_vol": fut_oi[who]["short"],
                    "opt_buy_call_yi": round(opt_amt[who]["buy_call"], 2),
                    "opt_sell_call_yi": round(opt_amt[who]["sell_call"], 2),
                    "opt_buy_put_yi": round(opt_amt[who]["buy_put"], 2),
                    "opt_sell_put_yi": round(opt_amt[who]["sell_put"], 2),
                }
                stmt = sqlite_upsert(InstitutionalDaily).values(**values).on_conflict_do_update(
                    index_elements=["date", "institution"],
                    set_={k: v for k, v in values.items() if k not in ("date", "institution")},
                )
                session.execute(stmt)
                rows_written += 1

        logger.info("[market_temp.institutional] %s: 寫入 %d row(3 法人)", d, rows_written)

    except Exception as e:
        err_msg = _redact(str(e))[:300]
        missing.append(d.isoformat())
        logger.warning("[market_temp.institutional] %s failed: %s", d, err_msg)

    record_sync_attempt(
        source="mt_institutional",
        success=(not err_msg),
        rows=rows_written,
        error=err_msg,
        missing=missing,
    )
    return {"date": d.isoformat(), "rows": rows_written, "missing": missing, "error": err_msg}


# ─────────────────────────────────────────────────────────────
# 3. 借券當日交易
# ─────────────────────────────────────────────────────────────
def sync_lending(target: date_type | None = None) -> dict:
    """每日 1 row:volume / deal_count / avg_fee_rate。"""
    d = target or _today_tw()
    rows_written = 0
    missing: list[str] = []
    err_msg: str | None = None

    try:
        twse_codes = _twse_stock_codes()
        sbl = finmind.request("TaiwanStockSecuritiesLending",
                              start_date=d.isoformat(), end_date=d.isoformat())
        rows = [r for r in sbl if r.get("stock_id") in twse_codes]
        if not rows:
            missing.append(d.isoformat())
            raise RuntimeError("無借券資料")

        volume = sum(int(r.get("volume") or 0) for r in rows)
        deal_count = len(rows)
        total_v = sum(int(r.get("volume") or 0) for r in rows) or 1
        weighted_fee = sum(float(r.get("fee_rate") or 0) * int(r.get("volume") or 0)
                           for r in rows) / total_v

        with session_scope() as session:
            stmt = sqlite_upsert(SecuritiesLendingDaily).values(
                date=d, volume=volume, deal_count=deal_count,
                avg_fee_rate=round(weighted_fee, 4),
            ).on_conflict_do_update(
                index_elements=["date"],
                set_={"volume": volume, "deal_count": deal_count,
                      "avg_fee_rate": round(weighted_fee, 4)},
            )
            session.execute(stmt)
        rows_written = 1
        logger.info("[market_temp.lending] %s: vol=%d cnt=%d fee=%.2f%%",
                    d, volume, deal_count, weighted_fee)

    except Exception as e:
        err_msg = _redact(str(e))[:300]
        if not missing:
            missing.append(d.isoformat())
        logger.warning("[market_temp.lending] %s failed: %s", d, err_msg)

    record_sync_attempt(
        source="mt_lending",
        success=(not err_msg),
        rows=rows_written,
        error=err_msg,
        missing=missing,
    )
    return {"date": d.isoformat(), "rows": rows_written, "missing": missing, "error": err_msg}


# ─────────────────────────────────────────────────────────────
# 4. 融資融券大盤合計 + 融資維持率(同一 dataset,一次抓兩個 table 寫)
# ─────────────────────────────────────────────────────────────
def sync_margin_short_and_maintenance(target: date_type | None = None) -> dict:
    """同時更新 margin_short_total + margin_maintenance(同個 FinMind dataset)。

    紀律 #14 — 不重抓兩次。
    """
    d = target or _today_tw()
    rows_written = 0
    missing: list[str] = []
    err_msg: str | None = None

    try:
        twse_codes = _twse_stock_codes()
        margin = finmind.request("TaiwanStockMarginPurchaseShortSale",
                                 start_date=d.isoformat(), end_date=d.isoformat())
        if not margin:
            missing.append(d.isoformat())
            raise RuntimeError("無融資融券資料")

        # 大盤合計(張)
        margin_balance = sum(int(r.get("MarginPurchaseTodayBalance") or 0)
                             for r in margin if r.get("stock_id") in twse_codes)
        short_balance = sum(int(r.get("ShortSaleTodayBalance") or 0)
                            for r in margin if r.get("stock_id") in twse_codes)

        # 融資維持率(XQ 含 ETF 口徑):分子 = 個股融資餘額 × 收盤 / 分母 = 大盤融資總額
        price_rows = finmind.request("TaiwanStockPrice",
                                     start_date=d.isoformat(), end_date=d.isoformat())
        price_by_id = {r["stock_id"]: float(r["close"]) for r in price_rows
                       if r.get("close") is not None}

        total_rows = finmind.request("TaiwanStockTotalMarginPurchaseShortSale",
                                     start_date=d.isoformat(), end_date=d.isoformat())
        den = 0.0
        for r in total_rows:
            if r.get("name") == "MarginPurchaseMoney" and r.get("TodayBalance") is not None:
                den = float(r["TodayBalance"])
                break

        num = 0.0
        cnt = 0
        for r in margin:
            sid = r.get("stock_id")
            if sid not in twse_codes:
                continue
            bal = float(r.get("MarginPurchaseTodayBalance") or 0)
            if bal <= 0:
                continue
            close = price_by_id.get(sid)
            if close is None or close <= 0:
                continue
            num += bal * 1000 * close
            cnt += 1

        if den <= 0:
            missing.append(d.isoformat())
            raise RuntimeError("分母 (大盤融資金額) = 0,無法算維持率")

        ratio_pct = round(num / den * 100, 2)
        num_yi = round(num / 1e8, 2)
        den_yi = round(den / 1e8, 2)

        with session_scope() as session:
            # margin_short_total
            session.execute(
                sqlite_upsert(MarginShortTotal).values(
                    date=d, margin_balance=margin_balance, short_balance=short_balance,
                ).on_conflict_do_update(
                    index_elements=["date"],
                    set_={"margin_balance": margin_balance, "short_balance": short_balance},
                )
            )
            # margin_maintenance
            session.execute(
                sqlite_upsert(MarginMaintenance).values(
                    date=d, ratio_pct=ratio_pct, numerator_yi=num_yi,
                    denominator_yi=den_yi, stock_count=cnt,
                ).on_conflict_do_update(
                    index_elements=["date"],
                    set_={"ratio_pct": ratio_pct, "numerator_yi": num_yi,
                          "denominator_yi": den_yi, "stock_count": cnt},
                )
            )
            rows_written = 2

        logger.info(
            "[market_temp.margin_short] %s: 融資 %d / 融券 %d 張",
            d, margin_balance, short_balance,
        )
        logger.info(
            "[market_temp.maintenance] %s: 維持率 %.2f%% (%d 支 / %.0f 億 / %.0f 億)",
            d, ratio_pct, cnt, num_yi, den_yi,
        )

    except Exception as e:
        err_msg = _redact(str(e))[:300]
        if not missing:
            missing.append(d.isoformat())
        logger.warning("[market_temp.margin_short] %s failed: %s", d, err_msg)

    # 兩個 source 都記
    record_sync_attempt(
        source="mt_margin_short",
        success=(not err_msg),
        rows=(1 if rows_written else 0),
        error=err_msg,
        missing=missing,
    )
    record_sync_attempt(
        source="mt_maintenance",
        success=(not err_msg),
        rows=(1 if rows_written else 0),
        error=err_msg,
        missing=missing,
    )
    return {"date": d.isoformat(), "rows": rows_written, "missing": missing, "error": err_msg}


# ─────────────────────────────────────────────────────────────
# 5. Backfill(admin 用)
# ─────────────────────────────────────────────────────────────
def backfill_range(start: date_type, end: date_type) -> dict:
    """逐日跑 5 個 sync。admin 一鍵 backfill 用。

    回傳 per-source 統計 + 失敗日期清單。
    """
    out = {
        "breadth": {"ok": 0, "fail": []},
        "institutional": {"ok": 0, "fail": []},
        "lending": {"ok": 0, "fail": []},
        "margin_short": {"ok": 0, "fail": []},
    }
    d = start
    while d <= end:
        # 1) breadth
        r = sync_breadth(d)
        if r.get("error"):
            out["breadth"]["fail"].append(d.isoformat())
        else:
            out["breadth"]["ok"] += 1
        # 2) institutional
        r = sync_institutional(d)
        if r.get("error"):
            out["institutional"]["fail"].append(d.isoformat())
        else:
            out["institutional"]["ok"] += 1
        # 3) lending
        r = sync_lending(d)
        if r.get("error"):
            out["lending"]["fail"].append(d.isoformat())
        else:
            out["lending"]["ok"] += 1
        # 4) margin_short + maintenance
        r = sync_margin_short_and_maintenance(d)
        if r.get("error"):
            out["margin_short"]["fail"].append(d.isoformat())
        else:
            out["margin_short"]["ok"] += 1

        d += timedelta(days=1)
    return out
