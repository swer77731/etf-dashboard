"""ETF 詳情頁指標 — 純讀本地 DB,不打外部 API(鐵律)。

提供:
- 多期間報酬(1m / 3m / 6m / ytd / 1y / 3y / since_inception)
- 近 1 年走勢序列(ETF + TAIEX,給 ECharts)
- 配息歷史(從 dividend table)
- 基本資訊(從 etf_list + 第一筆 K 棒推出上市日)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import select, func

from app.database import session_scope
from app.models.dividend import Dividend
from app.models.etf import ETF
from app.models.kbar import DailyKBar
from app.services.etf_classifier import label_of
from app.services.etf_universe import TAIEX_CODE
from app.services.ranking import (
    PERIOD_LABELS,
    apply_b_formula,
    apply_d_formula,
    _start_date_for,
    _index_period_return,
    _is_anomalous,
)


# Hybrid 策略:短期用 B 公式(對 YP 較貼),長期用 D 公式(adj_close 自動處理分割)
# 避免 0050 等做過 reverse split 的 ETF 在 1y / 3y 出現假跌
PERIODS_USING_B = {"1m", "3m", "ytd"}
PERIODS_USING_D = {"6m", "1y", "3y"}

logger = logging.getLogger(__name__)


# 詳情頁要顯示的 6 個期間(比首頁排行榜多)
DETAIL_PERIODS: list[str] = ["1m", "3m", "ytd", "1y", "3y", "since_inception"]
DETAIL_PERIOD_LABELS: dict[str, str] = {
    **PERIOD_LABELS,
    "since_inception": "成立至今",
}


@dataclass(slots=True)
class PeriodReturn:
    key: str
    label: str
    return_pct: float | None
    index_return_pct: float | None      # TAIEX 同期間
    vs_index_pct: float | None
    base_date: date | None
    base_close: float | None
    last_date: date | None
    last_close: float | None


def _first_kbar_date(session, etf_id: int) -> date | None:
    """ETF 在 DB 內的第一筆 K 棒日期 — 當作上市日近似值。"""
    return session.scalar(
        select(func.min(DailyKBar.date)).where(DailyKBar.etf_id == etf_id)
    )


def _compute_period(session, etf_id: int, period_key: str) -> PeriodReturn:
    label = DETAIL_PERIOD_LABELS[period_key]

    if period_key == "since_inception":
        # 用 adj_close 自動處理跨期間的分割 + 除權息(D 公式不需 anomaly filter,
        # 因為 adj_close 已正確處理 split,任何大數字都是真實報酬)
        first = session.execute(
            select(DailyKBar.date, DailyKBar.close, DailyKBar.adj_close)
            .where(DailyKBar.etf_id == etf_id)
            .where(DailyKBar.adj_close.is_not(None))
            .order_by(DailyKBar.date.asc()).limit(1)
        ).first()
        last = session.execute(
            select(DailyKBar.date, DailyKBar.close, DailyKBar.adj_close)
            .where(DailyKBar.etf_id == etf_id)
            .where(DailyKBar.adj_close.is_not(None))
            .order_by(DailyKBar.date.desc()).limit(1)
        ).first()
        if not first or not last or not first[2] or first[2] == 0:
            return PeriodReturn(period_key, label, None, None, None, None, None, None, None)
        base_date, base_raw, base_adj = first
        last_date, last_raw, last_adj = last
        ret = (last_adj / base_adj - 1) * 100   # D 公式 — adj_close,不過 anomaly filter
        idx_ret = _index_period_return(session, base_date)
        vs = (ret - idx_ret) if idx_ret is not None else None
        return PeriodReturn(period_key, label, ret, idx_ret, vs, base_date, base_raw, last_date, last_raw)

    # 標準 period — Hybrid 策略選 B 或 D
    period_start = _start_date_for(period_key)  # type: ignore[arg-type]
    using_d = period_key in PERIODS_USING_D
    if using_d:
        result = apply_d_formula(session, etf_id, period_start)
    else:
        b = apply_b_formula(session, etf_id, period_start)
        result = (b[0], b[1], b[2]) if b else None
    if not result:
        return PeriodReturn(period_key, label, None, None, None, None, None, None, None)
    base_date, last_date, ret_pct = result
    # D 公式不過 anomaly filter(adj_close 本身已正確處理 split,大數字是真的)
    if not using_d and _is_anomalous(ret_pct):
        ret_pct = None
    base_close = session.scalar(
        select(DailyKBar.close).where(DailyKBar.etf_id == etf_id).where(DailyKBar.date == base_date)
    )
    last_close = session.scalar(
        select(DailyKBar.close).where(DailyKBar.etf_id == etf_id).where(DailyKBar.date == last_date)
    )
    idx_ret = _index_period_return(session, period_start)
    vs = (ret_pct - idx_ret) if (ret_pct is not None and idx_ret is not None) else None
    return PeriodReturn(period_key, label, ret_pct, idx_ret, vs, base_date, base_close, last_date, last_close)


def get_etf_detail(code: str, today: date | None = None) -> dict | None:
    """組詳情頁全部資料 — 純讀本地 DB。回 None 表示找不到此 ETF。"""
    today = today or date.today()
    with session_scope() as session:
        etf = session.scalar(select(ETF).where(ETF.code == code.upper()))
        if not etf:
            return None

        first_kbar = _first_kbar_date(session, etf.id)

        # 6 個期間報酬
        periods = [_compute_period(session, etf.id, p) for p in DETAIL_PERIODS]

        # 近 1 年走勢序列(adj_close)— 給 ECharts
        one_year_ago = today - timedelta(days=370)
        trend_rows = session.execute(
            select(DailyKBar.date, DailyKBar.adj_close)
            .where(DailyKBar.etf_id == etf.id)
            .where(DailyKBar.date >= one_year_ago)
            .where(DailyKBar.adj_close.is_not(None))
            .order_by(DailyKBar.date.asc())
        ).all()
        trend_series = [
            {"date": d.isoformat(), "value": round(c, 4)}
            for d, c in trend_rows
        ]

        # 同期間 TAIEX(用 raw close)
        taiex = session.scalar(select(ETF).where(ETF.code == TAIEX_CODE))
        taiex_series: list[dict] = []
        if taiex:
            t_rows = session.execute(
                select(DailyKBar.date, DailyKBar.close)
                .where(DailyKBar.etf_id == taiex.id)
                .where(DailyKBar.date >= one_year_ago)
                .order_by(DailyKBar.date.asc())
            ).all()
            taiex_series = [
                {"date": d.isoformat(), "value": round(c, 2)}
                for d, c in t_rows
            ]

        # 配息歷史(全部)
        div_rows = session.execute(
            select(Dividend.ex_date, Dividend.cash_dividend, Dividend.payment_date, Dividend.fiscal_year)
            .where(Dividend.etf_id == etf.id)
            .order_by(Dividend.ex_date.desc())
        ).all()
        dividends = [
            {
                "ex_date": d.isoformat(),
                "cash": float(c),
                "payment_date": p.isoformat() if p else None,
                "fiscal_year": fy,
            }
            for d, c, p, fy in div_rows
        ]

        # 最新收盤(顯示「目前股價」)
        last_kbar = session.execute(
            select(DailyKBar.date, DailyKBar.open, DailyKBar.high, DailyKBar.low,
                   DailyKBar.close, DailyKBar.volume)
            .where(DailyKBar.etf_id == etf.id)
            .order_by(DailyKBar.date.desc()).limit(1)
        ).first()
        # 前一天用來算今日漲跌
        prev_close = session.scalar(
            select(DailyKBar.close)
            .where(DailyKBar.etf_id == etf.id)
            .where(DailyKBar.date < (last_kbar[0] if last_kbar else today))
            .order_by(DailyKBar.date.desc()).limit(1)
        )
        if last_kbar and prev_close:
            today_change_pct = (last_kbar[4] / prev_close - 1) * 100
        else:
            today_change_pct = None

        return {
            "code": etf.code,
            "name": etf.name,
            "category": etf.category,
            "category_label": label_of(etf.category),
            "issuer": etf.issuer,
            "index_tracked": etf.index_tracked,
            "first_data_date": first_kbar.isoformat() if first_kbar else None,
            "last_kbar": {
                "date": last_kbar[0].isoformat() if last_kbar else None,
                "open": last_kbar[1] if last_kbar else None,
                "high": last_kbar[2] if last_kbar else None,
                "low": last_kbar[3] if last_kbar else None,
                "close": last_kbar[4] if last_kbar else None,
                "volume": last_kbar[5] if last_kbar else None,
            } if last_kbar else None,
            "today_change_pct": today_change_pct,
            "periods": periods,
            "trend_etf": trend_series,
            "trend_taiex": taiex_series,
            "dividends": dividends,
            "dividend_count_1y": sum(
                1 for d in dividends if d["ex_date"] >= (today - timedelta(days=365)).isoformat()
            ),
            "dividend_sum_1y": sum(
                d["cash"] for d in dividends if d["ex_date"] >= (today - timedelta(days=365)).isoformat()
            ),
        }
