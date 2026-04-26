"""績效比較 — 多 ETF 自訂區間統計分析。100% 讀本地 DB。

核心指標(adj_close 一致口徑):
- 總報酬 / 年化報酬 / 年化波動率 / 夏普值 / Sortino / 最大回撤
- 最佳年 / 最差年(年度報酬)
- 累積報酬序列(期初 = 100,給 ECharts)

Step 3 預備需求(CLAUDE.md):
- 報酬一律用 adj_close
- 槓桿型線粗、反向型虛線
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from sqlalchemy import select

from app.database import session_scope
from app.models.etf import ETF
from app.models.kbar import DailyKBar
from app.services.etf_classifier import label_of, leverage_subtype

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE = 0.01    # 年化 1%(簡化,實際應抓國庫券利率)


@dataclass(slots=True)
class PerformanceStats:
    code: str
    name: str
    category: str
    category_label: str
    leverage_subtype: str | None       # "positive" / "inverse" / None
    leverage_multiplier: int | None
    base_date: str | None
    last_date: str | None
    days: int
    base_close: float | None
    last_close: float | None
    total_return_pct: float | None
    annualized_return_pct: float | None
    volatility_pct: float | None       # 年化波動
    sharpe: float | None
    sortino: float | None
    max_drawdown_pct: float | None
    best_year: str | None
    best_year_pct: float | None
    worst_year: str | None
    worst_year_pct: float | None
    series: list[dict] = field(default_factory=list)   # [{date, value}] 期初=100


def _safe_div(a: float, b: float) -> float | None:
    return a / b if b not in (0, None) else None


def _compute_one(etf: ETF, start: date, end: date) -> PerformanceStats | None:
    """單一 ETF 的全套統計。回 None 表示資料不足。"""
    with session_scope() as session:
        # 取期間內 adj_close 序列
        rows = session.execute(
            select(DailyKBar.date, DailyKBar.adj_close)
            .where(DailyKBar.etf_id == etf.id)
            .where(DailyKBar.date >= start)
            .where(DailyKBar.date <= end)
            .where(DailyKBar.adj_close.is_not(None))
            .order_by(DailyKBar.date.asc())
        ).all()

    if len(rows) < 5:
        return None

    dates = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    base = closes[0]
    last = closes[-1]

    # 標準化序列 (期初 = 100)
    series = [{"date": d.isoformat(), "value": round(c / base * 100, 3)}
              for d, c in zip(dates, closes)]

    days = (dates[-1] - dates[0]).days
    total_ret = (last / base - 1) * 100
    if days > 0:
        ann_ret = ((last / base) ** (365.25 / days) - 1) * 100
    else:
        ann_ret = None

    # 日報酬
    daily_returns = [
        closes[i] / closes[i - 1] - 1
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]

    # 年化波動率 (日報酬 stdev × sqrt(252))
    volatility = None
    sharpe = None
    sortino = None
    if len(daily_returns) >= 20:
        mean = sum(daily_returns) / len(daily_returns)
        var = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std = math.sqrt(var)
        volatility = std * math.sqrt(TRADING_DAYS_PER_YEAR) * 100

        if ann_ret is not None and volatility > 0:
            sharpe = (ann_ret - RISK_FREE_RATE * 100) / volatility

        # Sortino:只看下檔波動
        downside = [r for r in daily_returns if r < 0]
        if len(downside) >= 5:
            d_mean = 0   # 對 0 取下檔波動
            d_var = sum(r ** 2 for r in downside) / len(downside)
            d_std = math.sqrt(d_var)
            d_vol = d_std * math.sqrt(TRADING_DAYS_PER_YEAR) * 100
            if ann_ret is not None and d_vol > 0:
                sortino = (ann_ret - RISK_FREE_RATE * 100) / d_vol

    # 最大回撤
    max_dd = None
    if closes:
        peak = closes[0]
        worst = 0.0
        for c in closes:
            if c > peak:
                peak = c
            dd = (c / peak - 1) * 100
            if dd < worst:
                worst = dd
        max_dd = worst

    # 最佳/最差年(年度報酬)— 期間跨 ≥ 1 個完整年才算
    best_year = worst_year = None
    best_year_pct = worst_year_pct = None
    by_year: dict[int, list[tuple[date, float]]] = {}
    for d, c in zip(dates, closes):
        by_year.setdefault(d.year, []).append((d, c))
    yearly_returns: dict[int, float] = {}
    for y, lst in by_year.items():
        if len(lst) < 5:   # 太少筆數不算
            continue
        yearly_returns[y] = (lst[-1][1] / lst[0][1] - 1) * 100
    if len(yearly_returns) >= 1:
        b = max(yearly_returns.items(), key=lambda x: x[1])
        w = min(yearly_returns.items(), key=lambda x: x[1])
        best_year, best_year_pct = str(b[0]), b[1]
        worst_year, worst_year_pct = str(w[0]), w[1]

    sub, mult = leverage_subtype(etf.code, etf.name)

    return PerformanceStats(
        code=etf.code,
        name=etf.name,
        category=etf.category,
        category_label=label_of(etf.category),
        leverage_subtype=sub,
        leverage_multiplier=mult,
        base_date=dates[0].isoformat(),
        last_date=dates[-1].isoformat(),
        days=days,
        base_close=base,
        last_close=last,
        total_return_pct=total_ret,
        annualized_return_pct=ann_ret,
        volatility_pct=volatility,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd,
        best_year=best_year,
        best_year_pct=best_year_pct,
        worst_year=worst_year,
        worst_year_pct=worst_year_pct,
        series=series,
    )


def compare_etfs(codes: Iterable[str], start: date, end: date) -> dict:
    """比較多 ETF — 主要 entry。回 dict 給 template 用。"""
    code_list = []
    seen: set[str] = set()
    for c in codes:
        c = (c or "").strip().upper()
        if c and c not in seen:
            seen.add(c)
            code_list.append(c)

    found: list[PerformanceStats] = []
    not_found: list[str] = []
    insufficient: list[str] = []

    with session_scope() as session:
        etfs = {
            e.code: e
            for e in session.scalars(select(ETF).where(ETF.code.in_(code_list))).all()
        }

    for code in code_list:
        etf = etfs.get(code)
        if not etf:
            not_found.append(code)
            continue
        # detach 給 _compute_one 用(它會自己開 session)
        stats = _compute_one(etf, start, end)
        if stats is None:
            insufficient.append(code)
        else:
            found.append(stats)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "requested": code_list,
        "stats": found,
        "not_found": not_found,
        "insufficient": insufficient,
    }
