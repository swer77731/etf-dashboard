"""市場溫度計 view payload builder — 純讀本地 DB(資料主權鐵律)。

bug fix 2026-05-13:
- 原版 summary(1d/7d/30d 表格)用 `arr[-1]` 取最後一天,但 arr 從
  daily_kbar TAIEX 的 dates_full 對齊 — 如果 TAIEX 已有 today 但 sync
  還沒跑(today 早上),arr[-1] = 0(row miss)。
- 修法:summary 用 **各自 table 的 row,獨立計算**,不依賴 dates_full。
- chart array 仍用 dates_full 對齊加權指數(雙 Y 軸 X 軸同步)。
"""
from __future__ import annotations

from datetime import date as date_type, timedelta
from typing import Any

from sqlalchemy import select

from app.database import session_scope
from app.models.market_temperature import (
    MarginMaintenance,
    MarketBreadth,
    MarginShortTotal,
    SecuritiesLendingDaily,
    InstitutionalDaily,
)
from app.models.kbar import DailyKBar
from app.models.etf import ETF


def _fmt_short(d: date_type) -> str:
    return d.strftime("%m/%d")


def _sum_last(arr: list, n: int) -> float:
    if not arr:
        return 0
    return sum(arr[-n:]) if len(arr) >= n else sum(arr)


def _diff_n(arr: list, n: int) -> float:
    """最新 vs N 天前的差值(用於 OI 部位類別)。"""
    if not arr:
        return 0
    if len(arr) <= n:
        return arr[-1] - arr[0]
    return arr[-1] - arr[-1 - n]


def build_payload(days: int = 30) -> dict[str, Any]:
    """近 N 天資料 + 1/7/30 摘要(summary 用 table 各自 row,獨立於 dates_full)。"""
    today = date_type.today()
    start = today - timedelta(days=days * 2)  # 抓寬一點避免假日

    with session_scope() as session:
        # ──────── 1. gauge ────────
        latest_mm = session.scalar(
            select(MarginMaintenance).order_by(MarginMaintenance.date.desc()).limit(1)
        )
        gauge_value = round(latest_mm.ratio_pct, 2) if latest_mm else None
        gauge_date = latest_mm.date.isoformat() if latest_mm else None

        # ──────── 0. data freshness — 5 個 table 取最早 latest(最 stale) ────────
        from sqlalchemy import func as _func
        table_latests = []
        for cls in (MarginMaintenance, MarketBreadth, MarginShortTotal,
                    SecuritiesLendingDaily, InstitutionalDaily):
            ld = session.scalar(select(_func.max(cls.date)))
            table_latests.append(ld)
        non_null = [d for d in table_latests if d is not None]
        oldest_latest = min(non_null) if non_null else None
        if oldest_latest:
            data_age_days = (today - oldest_latest).days
            stale_date = oldest_latest.isoformat()
        else:
            data_age_days = 999
            stale_date = None

        # ──────── 2. dates_full (chart 用,從 TAIEX) ────────
        taiex = session.scalar(select(ETF).where(ETF.code == "TAIEX"))
        taiex_kbars = []
        if taiex:
            taiex_kbars = list(
                session.scalars(
                    select(DailyKBar)
                    .where(DailyKBar.etf_id == taiex.id, DailyKBar.date >= start)
                    .order_by(DailyKBar.date)
                )
            )
        taiex_kbars = taiex_kbars[-days:] if len(taiex_kbars) > days else taiex_kbars
        dates_full = [k.date for k in taiex_kbars]
        dates_short = [_fmt_short(d) for d in dates_full]
        taiex_arr = [round(float(k.close), 2) for k in taiex_kbars]

        # ──────── 3. 三大法人 — 抓最近 N 天(各 table 自己的 date) ────────
        inst_rows = list(
            session.scalars(
                select(InstitutionalDaily)
                .where(InstitutionalDaily.date >= start)
                .order_by(InstitutionalDaily.date)
            )
        )
        # 按 institution 分桶並排序
        inst_by_who: dict[str, list] = {"foreign": [], "trust": [], "dealer": []}
        for r in inst_rows:
            if r.institution in inst_by_who:
                inst_by_who[r.institution].append(r)
        for who in inst_by_who:
            inst_by_who[who].sort(key=lambda r: r.date)
            # 截最近 days
            inst_by_who[who] = inst_by_who[who][-days:]

        # ─ 現貨 summary(獨立) ─ 無資料 1d = None(template「—」)
        spot_summary = {}
        for who in ("foreign", "trust", "dealer"):
            rows = inst_by_who[who]
            arr = [float(r.spot_net_yi) for r in rows if r.spot_net_yi is not None]
            spot_summary[who] = {
                "1d": round(arr[-1], 1) if arr else None,
                "7d": round(_sum_last(arr, 7), 1) if arr else None,
                "30d": round(sum(arr), 1) if arr else None,
            }
        # total 用「至少一個 who 有值」就計算,全 None 才 None
        def _safe_sum(values):
            non_none = [v for v in values if v is not None]
            return round(sum(non_none), 1) if non_none else None
        spot_summary["total"] = {
            "1d": _safe_sum([spot_summary[w]["1d"] for w in ("foreign", "trust", "dealer")]),
            "7d": _safe_sum([spot_summary[w]["7d"] for w in ("foreign", "trust", "dealer")]),
            "30d": _safe_sum([spot_summary[w]["30d"] for w in ("foreign", "trust", "dealer")]),
        }

        # ─ 期貨 summary(淨多空 = long - short,需 row 有 fut_long_vol 才算) ─
        fut_summary = {}
        for who in ("foreign", "trust", "dealer"):
            rows = inst_by_who[who]
            arr = [int(r.fut_long_vol) - int(r.fut_short_vol or 0)
                   for r in rows if r.fut_long_vol is not None]
            fut_summary[who] = {
                "1d": arr[-1] if arr else None,
                "7d": _diff_n(arr, 7) if arr else None,
                "30d": _diff_n(arr, 30) if arr else None,
            }
        def _safe_sum_int(values):
            non_none = [v for v in values if v is not None]
            return sum(non_none) if non_none else None
        fut_summary["total"] = {
            "1d": _safe_sum_int([fut_summary[w]["1d"] for w in ("foreign", "trust", "dealer")]),
            "7d": _safe_sum_int([fut_summary[w]["7d"] for w in ("foreign", "trust", "dealer")]),
            "30d": _safe_sum_int([fut_summary[w]["30d"] for w in ("foreign", "trust", "dealer")]),
        }

        # ─ 選擇權 summary(僅外資 4 項) ─
        foreign_rows = inst_by_who["foreign"]
        opt_summary = {}
        for key, attr in [
            ("buy_call", "opt_buy_call_yi"),
            ("sell_call", "opt_sell_call_yi"),
            ("buy_put", "opt_buy_put_yi"),
            ("sell_put", "opt_sell_put_yi"),
        ]:
            arr = [float(getattr(r, attr)) for r in foreign_rows if getattr(r, attr) is not None]
            opt_summary[key] = {
                "1d": round(arr[-1], 2) if arr else None,
                "7d": round(_diff_n(arr, 7), 2) if arr else None,
                "30d": round(_diff_n(arr, 30), 2) if arr else None,
            }
        # total = (buy_call - sell_call) + (buy_put - sell_put)
        def _opt_total(period):
            parts = [opt_summary[k][period] for k in ("buy_call", "sell_call", "buy_put", "sell_put")]
            if any(p is None for p in parts):
                return None
            return round(parts[0] - parts[1] + parts[2] - parts[3], 2)
        opt_summary["total"] = {
            "1d": _opt_total("1d"),
            "7d": _opt_total("7d"),
            "30d": _opt_total("30d"),
        }

        # ─ chart arrays(用 dates_full 對齊加權指數) ─
        inst_by_date_who: dict = {}
        for r in inst_rows:
            inst_by_date_who.setdefault(r.date, {})[r.institution] = r

        def get_cell(d, who, attr):
            r = inst_by_date_who.get(d, {}).get(who)
            return getattr(r, attr, None) if r else None

        spot_foreign = [round(float(get_cell(d, "foreign", "spot_net_yi") or 0), 1) for d in dates_full]
        fut_long_arr = [int(get_cell(d, "foreign", "fut_long_vol") or 0) for d in dates_full]
        fut_short_arr = [int(get_cell(d, "foreign", "fut_short_vol") or 0) for d in dates_full]

        def opt_arr_for_chart(attr):
            return [float(get_cell(d, "foreign", attr) or 0) for d in dates_full]
        opt_buy_call_chart = opt_arr_for_chart("opt_buy_call_yi")
        opt_sell_call_chart = opt_arr_for_chart("opt_sell_call_yi")
        opt_buy_put_chart = opt_arr_for_chart("opt_buy_put_yi")
        opt_sell_put_chart = opt_arr_for_chart("opt_sell_put_yi")
        opt_call_net_arr = [round(c - sc, 2) for c, sc in zip(opt_buy_call_chart, opt_sell_call_chart)]
        opt_put_net_arr = [round(bp - sp, 2) for bp, sp in zip(opt_buy_put_chart, opt_sell_put_chart)]

        # ──────── 4. 漲跌家數(獨立 summary + chart 用 dates_full) ────────
        breadth_rows = list(
            session.scalars(
                select(MarketBreadth)
                .where(MarketBreadth.date >= start)
                .order_by(MarketBreadth.date)
            )
        )
        breadth_rows = breadth_rows[-days:]
        up_arr_self = [int(r.up_count) for r in breadth_rows]
        down_arr_self = [int(r.down_count) for r in breadth_rows]
        flat_arr_self = [int(r.flat_count) for r in breadth_rows]
        breadth_summary = {
            "up_1d": up_arr_self[-1] if up_arr_self else None,
            "down_1d": down_arr_self[-1] if down_arr_self else None,
            "flat_1d": flat_arr_self[-1] if flat_arr_self else None,
            "up_7d_avg": round(sum(up_arr_self[-7:]) / min(7, len(up_arr_self))) if up_arr_self else None,
            "down_7d_avg": round(sum(down_arr_self[-7:]) / min(7, len(down_arr_self))) if down_arr_self else None,
            "flat_7d_avg": round(sum(flat_arr_self[-7:]) / min(7, len(flat_arr_self))) if flat_arr_self else None,
            "up_30d_avg": round(sum(up_arr_self) / len(up_arr_self)) if up_arr_self else None,
            "down_30d_avg": round(sum(down_arr_self) / len(down_arr_self)) if down_arr_self else None,
            "flat_30d_avg": round(sum(flat_arr_self) / len(flat_arr_self)) if flat_arr_self else None,
        }
        # chart array 對齊 dates_full
        breadth_by = {b.date: b for b in breadth_rows}
        up_arr = [breadth_by[d].up_count if d in breadth_by else 0 for d in dates_full]
        down_arr = [breadth_by[d].down_count if d in breadth_by else 0 for d in dates_full]
        flat_arr = [breadth_by[d].flat_count if d in breadth_by else 0 for d in dates_full]

        # ──────── 5. 融資融券(獨立 summary + chart) ────────
        ms_rows = list(
            session.scalars(
                select(MarginShortTotal)
                .where(MarginShortTotal.date >= start)
                .order_by(MarginShortTotal.date)
            )
        )
        ms_rows = ms_rows[-days:]
        long_arr_self = [int(r.margin_balance) for r in ms_rows]
        short_arr_self = [int(r.short_balance) for r in ms_rows]
        margin_short_summary = {
            "long_1d": long_arr_self[-1] if long_arr_self else None,
            "long_7d_delta": int(_diff_n(long_arr_self, 7)) if long_arr_self else None,
            "long_30d_delta": int(_diff_n(long_arr_self, 30)) if long_arr_self else None,
            "short_1d": short_arr_self[-1] if short_arr_self else None,
            "short_7d_delta": int(_diff_n(short_arr_self, 7)) if short_arr_self else None,
            "short_30d_delta": int(_diff_n(short_arr_self, 30)) if short_arr_self else None,
        }
        def _ms_total(k1, k2):
            a = margin_short_summary[k1]
            b = margin_short_summary[k2]
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)
        margin_short_summary["total_1d"] = _ms_total("long_1d", "short_1d")
        margin_short_summary["total_7d"] = _ms_total("long_7d_delta", "short_7d_delta")
        margin_short_summary["total_30d"] = _ms_total("long_30d_delta", "short_30d_delta")
        # chart 對齊 dates_full(缺日填 None,ECharts 自動跳過不畫斷崖)
        ms_by = {r.date: r for r in ms_rows}
        long_arr = [int(ms_by[d].margin_balance) if d in ms_by else None for d in dates_full]
        short_arr = [int(ms_by[d].short_balance) if d in ms_by else None for d in dates_full]

        # ──────── 6. 借券(獨立 summary + chart) ────────
        sbl_rows = list(
            session.scalars(
                select(SecuritiesLendingDaily)
                .where(SecuritiesLendingDaily.date >= start)
                .order_by(SecuritiesLendingDaily.date)
            )
        )
        sbl_rows = sbl_rows[-days:]
        sbl_arr_self = [int(r.volume) for r in sbl_rows]
        sbl_count_self = [int(r.deal_count) for r in sbl_rows]
        sbl_fee_self = [float(r.avg_fee_rate) for r in sbl_rows]
        sbl_summary = {
            "vol_1d": sbl_arr_self[-1] if sbl_arr_self else None,
            "vol_7d_avg": round(sum(sbl_arr_self[-7:]) / min(7, len(sbl_arr_self))) if sbl_arr_self else None,
            "vol_30d_avg": round(sum(sbl_arr_self) / len(sbl_arr_self)) if sbl_arr_self else None,
            "count_1d": sbl_count_self[-1] if sbl_count_self else None,
            "count_7d_avg": round(sum(sbl_count_self[-7:]) / min(7, len(sbl_count_self))) if sbl_count_self else None,
            "count_30d_avg": round(sum(sbl_count_self) / len(sbl_count_self)) if sbl_count_self else None,
            "fee_1d": round(sbl_fee_self[-1], 2) if sbl_fee_self else None,
            "fee_7d_avg": round(sum(sbl_fee_self[-7:]) / min(7, len(sbl_fee_self)), 2) if sbl_fee_self else None,
            "fee_30d_avg": round(sum(sbl_fee_self) / len(sbl_fee_self), 2) if sbl_fee_self else None,
        }
        sbl_by = {r.date: r for r in sbl_rows}
        sbl_arr = [int(sbl_by[d].volume) if d in sbl_by else 0 for d in dates_full]

    return {
        "gauge_value": gauge_value,
        "gauge_date": gauge_date,
        "data_age_days": data_age_days,
        "stale_date": stale_date,
        "dates_short": dates_short,
        "taiex": taiex_arr,
        "spot_foreign": spot_foreign,
        "spot_summary": spot_summary,
        "fut_long_arr": fut_long_arr,
        "fut_short_arr": fut_short_arr,
        "fut_summary": fut_summary,
        "opt_call_net_arr": opt_call_net_arr,
        "opt_put_net_arr": opt_put_net_arr,
        "opt_summary": opt_summary,
        "up_arr": up_arr,
        "down_arr": down_arr,
        "breadth_summary": breadth_summary,
        "long_arr": long_arr,
        "short_arr": short_arr,
        "margin_short_summary": margin_short_summary,
        "sbl_arr": sbl_arr,
        "sbl_summary": sbl_summary,
    }
