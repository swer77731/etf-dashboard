"""配息相關計算 — 統一入口,所有殖利率邏輯只能透過這個 module。

CLAUDE.md 鐵律:
- 100% 讀本地 DB,不打外網
- 殖利率一律用「前一日收盤」算,標明資料截至日期
- 新 ETF 首次配息只顯示已公告金額,不做歷史推估

提供:
- compute_yield(amount, base_price)
- detect_frequency(etf_id, lookback_months=18) → 'monthly' / 'quarterly' / 'semi-annual' / 'annual' / None
- compute_annualized_yield(amount, frequency, base_price)
- get_upcoming_dividends(days=14) → 即將除息列表(按頻率分組用)
- get_yield_range(etf_id, days=30) → 過去 N 天每日推估殖利率區間
- get_history_summary(etf_id, years=5) → 過去 N 年配息細項 + 年度小計
- get_next_announced(etf_id) → 該 ETF 下一個已公告未除息的記錄
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import desc, select

from app.database import session_scope
from app.models.dividend import Dividend
from app.models.etf import ETF
from app.models.kbar import DailyKBar

logger = logging.getLogger(__name__)


Frequency = Literal["monthly", "quarterly", "semi-annual", "annual"]

FREQ_LABEL_ZH = {
    "monthly":     "月配",
    "quarterly":   "季配",
    "semi-annual": "半年配",
    "annual":      "年配",
}

FREQ_PER_YEAR = {
    "monthly":     12,
    "quarterly":   4,
    "semi-annual": 2,
    "annual":      1,
}

# 三大顯示分組(首頁公布欄 UI 用)
GROUP_MONTHLY    = "monthly"
GROUP_QUARTERLY  = "quarterly"      # 季配 + 雙月配
GROUP_LONG       = "long"           # 半年配 + 年配
GROUP_LABELS = {
    GROUP_MONTHLY:   ("月配(月月領)",   "適合想月月領薪水的客戶"),
    GROUP_QUARTERLY: ("雙月配 / 季配",   "平衡型現金流"),
    GROUP_LONG:      ("半年配 / 年配",   "大筆入袋"),
}


def compute_yield(amount: float | None, base_price: float | None) -> float | None:
    """單次殖利率(%)= 配息金額 / 基準價格 × 100。任一為 None / 0 → None。"""
    if not amount or not base_price or base_price <= 0:
        return None
    return round(amount / base_price * 100, 4)


def compute_annualized_yield(amount: float | None, frequency: Frequency | None,
                             base_price: float | None) -> float | None:
    """估算年化殖利率(%)= 單次殖利率 × 一年配幾次。frequency=None → 不能推。"""
    if not frequency or frequency not in FREQ_PER_YEAR:
        return None
    one = compute_yield(amount, base_price)
    if one is None:
        return None
    return round(one * FREQ_PER_YEAR[frequency], 4)


def detect_frequency(etf_id: int, lookback_months: int = 18) -> Frequency | None:
    """看過去 N 個月的 ex_date 數量推測配息頻率。

    rule of thumb(以 18 個月為窗):
    - >= 14 次 → 月配
    - 5 ~ 13 次 → 季配 / 雙月配(統一回 quarterly)
    - 2 ~ 4 次 → 半年配
    - 1 次 → 年配
    - 0 次 → None(可能新 ETF 沒配息)
    """
    cutoff = date.today() - timedelta(days=int(lookback_months * 30.5))
    with session_scope() as s:
        rows = s.scalars(
            select(Dividend.ex_date)
            .where(Dividend.etf_id == etf_id)
            .where(Dividend.ex_date >= cutoff)
            .where(Dividend.ex_date <= date.today())   # 只看已實現
            .where(Dividend.cash_dividend > 0)
        ).all()
    n = len(rows)
    # 月配 ETF 在 18 個月應該配 18 次,留些容差
    if n >= 14:
        return "monthly"
    if n >= 5:
        return "quarterly"
    if n >= 2:
        return "semi-annual"
    if n >= 1:
        return "annual"
    return None


def freq_to_group(freq: Frequency | None) -> str | None:
    """配息頻率對應到首頁三大分組。"""
    if freq == "monthly":
        return GROUP_MONTHLY
    if freq == "quarterly":
        return GROUP_QUARTERLY
    if freq in ("semi-annual", "annual"):
        return GROUP_LONG
    return None


def _get_close_on_or_before(session, etf_id: int, target_date: date) -> tuple[date, float] | None:
    """取 target_date 當天或之前最後一筆收盤(原始 close,跟券商 APP 一致)。"""
    row = session.execute(
        select(DailyKBar.date, DailyKBar.close)
        .where(DailyKBar.etf_id == etf_id)
        .where(DailyKBar.date <= target_date)
        .order_by(desc(DailyKBar.date))
        .limit(1)
    ).first()
    if not row:
        return None
    return row[0], float(row[1])


def _get_latest_close(session, etf_id: int) -> tuple[date, float] | None:
    """取最後一筆收盤(原始)。"""
    row = session.execute(
        select(DailyKBar.date, DailyKBar.close)
        .where(DailyKBar.etf_id == etf_id)
        .order_by(desc(DailyKBar.date))
        .limit(1)
    ).first()
    if not row:
        return None
    return row[0], float(row[1])


@dataclass(slots=True)
class UpcomingDividend:
    code: str
    name: str
    category: str
    ex_date: date
    payment_date: date | None
    cash_dividend: float
    announce_date: date | None
    days_to_ex: int
    days_to_pay: int | None
    latest_close: float | None
    latest_close_date: date | None
    yield_pct: float | None
    frequency: Frequency | None
    frequency_label: str
    group: str


def get_upcoming_dividends(days: int = 14, today: date | None = None,
                           past_days: int = 0) -> dict:
    """未來 N 天即將除息的 ETF,按 monthly / quarterly / long 三組分。

    Args:
        days: 未來幾天(>=0)
        past_days: 也帶進過去幾天的記錄(>0 時 UI 應該標示「已除息」),
                   用於 TWSE 公告爬蟲還沒上線時的 fallback 顯示

    回:
    - { groups: { monthly: [...], quarterly: [...], long: [...] }, total: int,
        as_of: 'YYYY-MM-DD', has_future: bool }
    """
    today = today or date.today()
    start = today - timedelta(days=past_days) if past_days > 0 else today
    end = today + timedelta(days=days)
    groups: dict[str, list[dict]] = {GROUP_MONTHLY: [], GROUP_QUARTERLY: [], GROUP_LONG: []}

    with session_scope() as s:
        rows = s.execute(
            select(Dividend, ETF)
            .join(ETF, Dividend.etf_id == ETF.id)
            .where(ETF.is_active.is_(True))
            .where(ETF.category != "index")
            .where(Dividend.ex_date >= start)
            .where(Dividend.ex_date <= end)
            # 收 NULL(待公告金額)+ 排除 0 元 / 負數
            # plan Q1 by user: 「用戶要的是『知道幾號除息,金額還在等』」
            .where((Dividend.cash_dividend > 0) | Dividend.cash_dividend.is_(None))
            .order_by(Dividend.ex_date.asc())
        ).all()

        for d, etf in rows:
            freq = detect_frequency(etf.id)
            grp = freq_to_group(freq) or GROUP_LONG   # 推不出來 → 歸長間隔
            latest = _get_latest_close(s, etf.id)
            latest_close = latest[1] if latest else None
            latest_dt    = latest[0] if latest else None
            yield_pct = compute_yield(d.cash_dividend, latest_close) if latest_close else None

            up = UpcomingDividend(
                code=etf.code,
                name=etf.name,
                category=etf.category,
                ex_date=d.ex_date,
                payment_date=d.payment_date,
                cash_dividend=d.cash_dividend,
                announce_date=d.announce_date,
                days_to_ex=(d.ex_date - today).days,
                days_to_pay=((d.payment_date - today).days if d.payment_date else None),
                latest_close=latest_close,
                latest_close_date=latest_dt,
                yield_pct=yield_pct,
                frequency=freq,
                frequency_label=FREQ_LABEL_ZH.get(freq or "", "—"),
                group=grp,
            )
            groups[grp].append({
                "code": up.code,
                "name": up.name,
                "category": up.category,
                "ex_date": up.ex_date.isoformat(),
                "payment_date": up.payment_date.isoformat() if up.payment_date else None,
                "cash_dividend": up.cash_dividend,
                "announce_date": up.announce_date.isoformat() if up.announce_date else None,
                "days_to_ex": up.days_to_ex,
                "days_to_pay": up.days_to_pay,
                "latest_close": up.latest_close,
                "latest_close_date": up.latest_close_date.isoformat() if up.latest_close_date else None,
                "yield_pct": up.yield_pct,
                "frequency": up.frequency,
                "frequency_label": up.frequency_label,
            })

    return {
        "as_of": today.isoformat(),
        "window_days": days,
        "total": sum(len(v) for v in groups.values()),
        "groups": groups,
    }


def get_dividends_in_range(start: date, end: date) -> list[dict]:
    """區間內(含端點)所有 active 非 index ETF 的配息事件,給 /dividend-calendar 用。

    依 ex_date 升冪。NULL cash_dividend 也帶進(代表「已公告日期但金額未定」)。
    """
    out: list[dict] = []
    with session_scope() as s:
        rows = s.execute(
            select(Dividend, ETF)
            .join(ETF, Dividend.etf_id == ETF.id)
            .where(ETF.is_active.is_(True))
            .where(ETF.category != "index")
            .where(Dividend.ex_date >= start)
            .where(Dividend.ex_date <= end)
            .where((Dividend.cash_dividend > 0) | Dividend.cash_dividend.is_(None))
            .order_by(Dividend.ex_date.asc(), ETF.code.asc())
        ).all()

        for d, etf in rows:
            out.append({
                "code": etf.code,
                "name": etf.name,
                "category": etf.category,
                "ex_date": d.ex_date.isoformat(),
                "payment_date": d.payment_date.isoformat() if d.payment_date else None,
                "cash_dividend": d.cash_dividend,
                "announce_date": d.announce_date.isoformat() if d.announce_date else None,
            })
    return out


@dataclass(slots=True)
class YieldRange:
    days: int
    min_pct: float | None
    max_pct: float | None
    avg_pct: float | None
    samples: int


def get_yield_range(etf_id: int, dividend_amount: float, days: int = 30) -> YieldRange:
    """過去 N 天的每日推估殖利率(以該 dividend_amount 為分子)的 min / max / avg。

    用:詳情頁顯示「過去 30 天區間 2.65% ~ 2.92%(平均 2.78%)」
    """
    end = date.today()
    start = end - timedelta(days=days)
    with session_scope() as s:
        closes = s.scalars(
            select(DailyKBar.close)
            .where(DailyKBar.etf_id == etf_id)
            .where(DailyKBar.date >= start)
            .where(DailyKBar.date <= end)
        ).all()
    closes = [float(c) for c in closes if c]
    if not closes or dividend_amount <= 0:
        return YieldRange(days=days, min_pct=None, max_pct=None, avg_pct=None, samples=0)
    yields = [dividend_amount / c * 100 for c in closes if c > 0]
    return YieldRange(
        days=days,
        min_pct=round(min(yields), 4),
        max_pct=round(max(yields), 4),
        avg_pct=round(statistics.mean(yields), 4),
        samples=len(yields),
    )


@dataclass(slots=True)
class NextAnnounced:
    """該 ETF 下一個「已公告但未除息」的記錄。"""
    ex_date: date
    payment_date: date | None
    announce_date: date | None
    cash_dividend: float
    days_to_ex: int
    days_to_pay: int | None
    announce_close_date: date | None       # 公告日當天/之前最後收盤
    announce_close: float | None
    announce_yield_pct: float | None
    latest_close_date: date | None
    latest_close: float | None
    latest_yield_pct: float | None         # 「即時殖利率」(隨股價浮動)
    frequency: Frequency | None
    frequency_label: str


def get_next_announced(etf_id: int, today: date | None = None,
                       fallback_to_recent: bool = True) -> NextAnnounced | None:
    """ETF 下一個「未除息但已公告」的配息記錄。

    fallback_to_recent=True 時,若無未來記錄,改回「最近一次已除息」(供詳情頁 fallback)。
    沒有任何配息歷史 → None。
    """
    today = today or date.today()
    with session_scope() as s:
        # 1. 先找未來 — 收待公告(cash_dividend IS NULL)+ 排除 0/負
        d = s.scalars(
            select(Dividend)
            .where(Dividend.etf_id == etf_id)
            .where(Dividend.ex_date >= today)
            .where((Dividend.cash_dividend > 0) | Dividend.cash_dividend.is_(None))
            .order_by(Dividend.ex_date.asc())
            .limit(1)
        ).first()
        # 2. fallback 到最近一次過去 — **保留排除 NULL**
        # (歷史已實現的配息不該是 NULL,NULL = 壞資料,fallback 應跳過)
        if not d and fallback_to_recent:
            d = s.scalars(
                select(Dividend)
                .where(Dividend.etf_id == etf_id)
                .where(Dividend.ex_date < today)
                .where(Dividend.cash_dividend > 0)
                .order_by(Dividend.ex_date.desc())
                .limit(1)
            ).first()
        if not d:
            return None

        announce_dt = d.announce_date
        announce_close: tuple[date, float] | None = None
        if announce_dt:
            announce_close = _get_close_on_or_before(s, etf_id, announce_dt)
        latest = _get_latest_close(s, etf_id)

    freq = detect_frequency(etf_id)
    return NextAnnounced(
        ex_date=d.ex_date,
        payment_date=d.payment_date,
        announce_date=d.announce_date,
        cash_dividend=d.cash_dividend,
        days_to_ex=(d.ex_date - today).days,
        days_to_pay=((d.payment_date - today).days if d.payment_date else None),
        announce_close_date=announce_close[0] if announce_close else None,
        announce_close=announce_close[1] if announce_close else None,
        announce_yield_pct=(compute_yield(d.cash_dividend, announce_close[1]) if announce_close else None),
        latest_close_date=latest[0] if latest else None,
        latest_close=latest[1] if latest else None,
        latest_yield_pct=(compute_yield(d.cash_dividend, latest[1]) if latest else None),
        frequency=freq,
        frequency_label=FREQ_LABEL_ZH.get(freq or "", "—"),
    )


@dataclass(slots=True)
class HistoryRow:
    ex_date: date
    payment_date: date | None
    cash_dividend: float
    announce_date: date | None


@dataclass(slots=True)
class YearSummary:
    year: int
    count: int
    total_cash: float
    avg_per_event: float
    estimated_annual_yield_pct: float | None      # 用該年最後一日收盤估


def get_history_summary(etf_id: int, years: int = 5,
                        today: date | None = None) -> dict:
    """過去 N 年配息細項(已實現)+ 年度小計,給詳情頁配息歷史卡用。

    回:
    - rows: 每筆配息(由新到舊)
    - by_year: 每年小計(由新到舊)
    """
    today = today or date.today()
    cutoff = today.replace(year=today.year - years, month=1, day=1)

    with session_scope() as s:
        rows = s.scalars(
            select(Dividend)
            .where(Dividend.etf_id == etf_id)
            .where(Dividend.ex_date >= cutoff)
            .where(Dividend.ex_date <= today)
            .where(Dividend.cash_dividend > 0)
            .order_by(Dividend.ex_date.desc())
        ).all()

        # 該年最後一日收盤(用來估年度殖利率)
        year_last_close: dict[int, float] = {}
        years_seen = sorted({r.ex_date.year for r in rows})
        for y in years_seen:
            yr_end = date(y, 12, 31)
            row = s.execute(
                select(DailyKBar.close)
                .where(DailyKBar.etf_id == etf_id)
                .where(DailyKBar.date <= min(yr_end, today))
                .order_by(desc(DailyKBar.date))
                .limit(1)
            ).first()
            if row:
                year_last_close[y] = float(row[0])

    detail = [{
        "ex_date": r.ex_date.isoformat(),
        "payment_date": r.payment_date.isoformat() if r.payment_date else None,
        "cash_dividend": r.cash_dividend,
        "announce_date": r.announce_date.isoformat() if r.announce_date else None,
    } for r in rows]

    by_year_acc: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        by_year_acc[r.ex_date.year].append(r.cash_dividend)

    by_year: list[dict] = []
    for y in sorted(by_year_acc.keys(), reverse=True):
        amounts = by_year_acc[y]
        total_cash = round(sum(amounts), 4)
        last_close = year_last_close.get(y)
        ann_yield = (round(total_cash / last_close * 100, 4)
                     if last_close and last_close > 0 else None)
        by_year.append({
            "year": y,
            "count": len(amounts),
            "total_cash": total_cash,
            "avg_per_event": round(total_cash / len(amounts), 4),
            "estimated_annual_yield_pct": ann_yield,
            "year_end_close": last_close,
        })

    return {
        "rows": detail,
        "by_year": by_year,
        "total_events": len(detail),
        "as_of": today.isoformat(),
        "lookback_years": years,
    }
