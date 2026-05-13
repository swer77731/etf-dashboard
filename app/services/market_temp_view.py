"""市場溫度計 view payload builder — 純讀本地 DB(資料主權鐵律)。

從 5 個 table 抓最近 N 天資料,組裝 template ctx。
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
    if not arr:
        return 0
    if len(arr) <= n:
        return arr[-1] - arr[0]
    return arr[-1] - arr[-1 - n]


def build_payload(days: int = 30) -> dict[str, Any]:
    """近 N 天資料 + 1/7/30 摘要。"""
    today = date_type.today()
    start = today - timedelta(days=days * 2)  # 抓寬一點避免假日

    with session_scope() as session:
        # ──────── 1. gauge:最新一日融資維持率 ────────
        latest_mm = session.scalar(
            select(MarginMaintenance).order_by(MarginMaintenance.date.desc()).limit(1)
        )
        gauge_value = round(latest_mm.ratio_pct, 2) if latest_mm else None
        gauge_date = latest_mm.date.isoformat() if latest_mm else None

        # ──────── 2. 共用日期(TAIEX 為基準找近 N 個交易日)────────
        # 用 etf_list 找 TAIEX 然後 daily_kbar
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

        # ──────── 3. 現貨 三大法人(從 institutional_daily filter dates) ────────
        inst_rows = list(
            session.scalars(
                select(InstitutionalDaily)
                .where(InstitutionalDaily.date >= dates_full[0] if dates_full else start)
                .order_by(InstitutionalDaily.date)
            )
        ) if dates_full else []

        # 整理成 {date: {who: row}}
        inst_by: dict = {}
        for r in inst_rows:
            inst_by.setdefault(r.date, {})[r.institution] = r

        def arr_for(who: str, attr: str) -> list:
            out = []
            for d in dates_full:
                r = inst_by.get(d, {}).get(who)
                v = getattr(r, attr, None) if r else None
                out.append(v if v is not None else 0)
            return out

        spot_foreign = arr_for("foreign", "spot_net_yi")

        # 1/7/30 表格 — 現貨
        spot_summary = {}
        for who in ("foreign", "trust", "dealer"):
            arr = arr_for(who, "spot_net_yi")
            spot_summary[who] = {
                "1d": round(arr[-1] if arr else 0, 1),
                "7d": round(_sum_last(arr, 7), 1),
                "30d": round(sum(arr), 1),
            }
        # 合計
        spot_summary["total"] = {
            "1d": round(sum(spot_summary[w]["1d"] for w in ("foreign", "trust", "dealer")), 1),
            "7d": round(sum(spot_summary[w]["7d"] for w in ("foreign", "trust", "dealer")), 1),
            "30d": round(sum(spot_summary[w]["30d"] for w in ("foreign", "trust", "dealer")), 1),
        }

        # 期貨(淨多空 = long - short)
        def fut_net(who: str) -> list[int]:
            out = []
            for d in dates_full:
                r = inst_by.get(d, {}).get(who)
                if r and r.fut_long_vol is not None:
                    out.append(int(r.fut_long_vol) - int(r.fut_short_vol or 0))
                else:
                    out.append(0)
            return out

        fut_summary = {}
        for who in ("foreign", "trust", "dealer"):
            arr = fut_net(who)
            fut_summary[who] = {
                "1d": arr[-1] if arr else 0,
                "7d": _diff_n(arr, 7),
                "30d": _diff_n(arr, 30),
            }
        # 合計
        fut_summary["total"] = {
            "1d": sum(fut_summary[w]["1d"] for w in ("foreign", "trust", "dealer")),
            "7d": sum(fut_summary[w]["7d"] for w in ("foreign", "trust", "dealer")),
            "30d": sum(fut_summary[w]["30d"] for w in ("foreign", "trust", "dealer")),
        }
        # chart:外資 long/short 各別 + 加權指數
        fut_long_arr = [
            int(inst_by.get(d, {}).get("foreign").fut_long_vol or 0)
            if inst_by.get(d, {}).get("foreign") else 0 for d in dates_full
        ]
        fut_short_arr = [
            int(inst_by.get(d, {}).get("foreign").fut_short_vol or 0)
            if inst_by.get(d, {}).get("foreign") else 0 for d in dates_full
        ]

        # 選擇權外資 4 項
        def opt_arr(attr: str) -> list[float]:
            out = []
            for d in dates_full:
                r = inst_by.get(d, {}).get("foreign")
                out.append(float(getattr(r, attr, None) or 0) if r else 0.0)
            return out

        opt_buy_call_arr = opt_arr("opt_buy_call_yi")
        opt_sell_call_arr = opt_arr("opt_sell_call_yi")
        opt_buy_put_arr = opt_arr("opt_buy_put_yi")
        opt_sell_put_arr = opt_arr("opt_sell_put_yi")
        # call - put net(chart 用 2 條 bar:Call 淨 / Put 淨)
        opt_call_net_arr = [round(c - sc, 2) for c, sc in zip(opt_buy_call_arr, opt_sell_call_arr)]
        opt_put_net_arr = [round(bp - sp, 2) for bp, sp in zip(opt_buy_put_arr, opt_sell_put_arr)]

        opt_summary = {}
        for key, arr in [
            ("buy_call", opt_buy_call_arr),
            ("sell_call", opt_sell_call_arr),
            ("buy_put", opt_buy_put_arr),
            ("sell_put", opt_sell_put_arr),
        ]:
            opt_summary[key] = {
                "1d": round(arr[-1] if arr else 0, 2),
                "7d": round(_diff_n(arr, 7), 2),
                "30d": round(_diff_n(arr, 30), 2),
            }
        opt_summary["total"] = {
            "1d": round(opt_summary["buy_call"]["1d"] - opt_summary["sell_call"]["1d"]
                        + opt_summary["buy_put"]["1d"] - opt_summary["sell_put"]["1d"], 2),
            "7d": round(opt_summary["buy_call"]["7d"] - opt_summary["sell_call"]["7d"]
                        + opt_summary["buy_put"]["7d"] - opt_summary["sell_put"]["7d"], 2),
            "30d": round(opt_summary["buy_call"]["30d"] - opt_summary["sell_call"]["30d"]
                         + opt_summary["buy_put"]["30d"] - opt_summary["sell_put"]["30d"], 2),
        }

        # ──────── 4. 漲跌家數 ────────
        breadth_rows = list(
            session.scalars(
                select(MarketBreadth)
                .where(MarketBreadth.date >= dates_full[0] if dates_full else start)
                .order_by(MarketBreadth.date)
            )
        ) if dates_full else []
        breadth_by = {b.date: b for b in breadth_rows}
        up_arr = [breadth_by[d].up_count if d in breadth_by else 0 for d in dates_full]
        down_arr = [breadth_by[d].down_count if d in breadth_by else 0 for d in dates_full]
        flat_arr = [breadth_by[d].flat_count if d in breadth_by else 0 for d in dates_full]

        n_real = len([x for x in up_arr if x > 0]) or 1  # 防 div0
        breadth_summary = {
            "up_1d": up_arr[-1] if up_arr else 0,
            "down_1d": down_arr[-1] if down_arr else 0,
            "flat_1d": flat_arr[-1] if flat_arr else 0,
            "up_7d_avg": round(sum(up_arr[-7:]) / max(1, min(7, len(up_arr)))),
            "down_7d_avg": round(sum(down_arr[-7:]) / max(1, min(7, len(down_arr)))),
            "flat_7d_avg": round(sum(flat_arr[-7:]) / max(1, min(7, len(flat_arr)))),
            "up_30d_avg": round(sum(up_arr) / max(1, len(up_arr))),
            "down_30d_avg": round(sum(down_arr) / max(1, len(down_arr))),
            "flat_30d_avg": round(sum(flat_arr) / max(1, len(flat_arr))),
        }

        # ──────── 5. 融資融券 ────────
        ms_rows = list(
            session.scalars(
                select(MarginShortTotal)
                .where(MarginShortTotal.date >= dates_full[0] if dates_full else start)
                .order_by(MarginShortTotal.date)
            )
        ) if dates_full else []
        ms_by = {r.date: r for r in ms_rows}
        long_arr = [int(ms_by[d].margin_balance) if d in ms_by else 0 for d in dates_full]
        short_arr = [int(ms_by[d].short_balance) if d in ms_by else 0 for d in dates_full]
        margin_short_summary = {
            "long_1d": long_arr[-1] if long_arr else 0,
            "long_7d_delta": int(_diff_n(long_arr, 7)),
            "long_30d_delta": int(_diff_n(long_arr, 30)),
            "short_1d": short_arr[-1] if short_arr else 0,
            "short_7d_delta": int(_diff_n(short_arr, 7)),
            "short_30d_delta": int(_diff_n(short_arr, 30)),
        }
        margin_short_summary["total_1d"] = margin_short_summary["long_1d"] + margin_short_summary["short_1d"]
        margin_short_summary["total_7d"] = margin_short_summary["long_7d_delta"] + margin_short_summary["short_7d_delta"]
        margin_short_summary["total_30d"] = margin_short_summary["long_30d_delta"] + margin_short_summary["short_30d_delta"]

        # ──────── 6. 借券 ────────
        sbl_rows = list(
            session.scalars(
                select(SecuritiesLendingDaily)
                .where(SecuritiesLendingDaily.date >= dates_full[0] if dates_full else start)
                .order_by(SecuritiesLendingDaily.date)
            )
        ) if dates_full else []
        sbl_by = {r.date: r for r in sbl_rows}
        sbl_arr = [int(sbl_by[d].volume) if d in sbl_by else 0 for d in dates_full]
        sbl_count_arr = [int(sbl_by[d].deal_count) if d in sbl_by else 0 for d in dates_full]
        sbl_fee_arr = [float(sbl_by[d].avg_fee_rate) if d in sbl_by else 0.0 for d in dates_full]

        sbl_summary = {
            "vol_1d": sbl_arr[-1] if sbl_arr else 0,
            "vol_7d_avg": round(sum(sbl_arr[-7:]) / max(1, min(7, len(sbl_arr)))),
            "vol_30d_avg": round(sum(sbl_arr) / max(1, len(sbl_arr))),
            "count_1d": sbl_count_arr[-1] if sbl_count_arr else 0,
            "count_7d_avg": round(sum(sbl_count_arr[-7:]) / max(1, min(7, len(sbl_count_arr)))),
            "count_30d_avg": round(sum(sbl_count_arr) / max(1, len(sbl_count_arr))),
            "fee_1d": round(sbl_fee_arr[-1] if sbl_fee_arr else 0, 2),
            "fee_7d_avg": round(sum(sbl_fee_arr[-7:]) / max(1, min(7, len(sbl_fee_arr))), 2),
            "fee_30d_avg": round(sum(sbl_fee_arr) / max(1, len(sbl_fee_arr)), 2),
        }

    return {
        "gauge_value": gauge_value,
        "gauge_date": gauge_date,
        "dates_short": dates_short,
        "taiex": taiex_arr,
        "spot_foreign": [round(v, 1) for v in spot_foreign],
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
