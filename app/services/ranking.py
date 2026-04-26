"""排行榜查詢:純讀本地 SQLite,**所有報酬計算用 adj_close**。

提供:
- ETF 在指定期間的報酬 (相對該期間內第一個交易日的還原價)
- 大盤(TAIEX)同期間報酬
- 「vs 大盤」= ETF 報酬 - TAIEX 報酬(百分點)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import func, select

from app.database import session_scope
from app.models.dividend import Dividend
from app.models.etf import ETF
from app.models.kbar import DailyKBar
from app.services.etf_classifier import label_of
from app.services.etf_universe import TAIEX_CODE

logger = logging.getLogger(__name__)


PeriodKey = Literal["1m", "3m", "ytd", "1y", "3y"]

PERIOD_LABELS: dict[str, str] = {
    "1m":  "近 1 個月",
    "3m":  "近 3 個月",
    "ytd": "今年以來",
    "1y":  "近 1 年",
    "3y":  "近 3 年",
}

# 異常值防護網 — 任何 |return| 超過此區間自動排除排行榜並 log warning。
# 主要防範:反向 ETF 做合併單位(reverse split)會使 raw close 跳幾百 %,
# B 公式以 raw close 計算就被污染。長遠來看應另外處理 split,目前先剔除。
ANOMALY_RETURN_UPPER = 200.0   # > +200%
ANOMALY_RETURN_LOWER = -80.0   # < -80%


def _is_anomalous(period_ret: float | None) -> bool:
    if period_ret is None:
        return False
    return period_ret > ANOMALY_RETURN_UPPER or period_ret < ANOMALY_RETURN_LOWER


def _start_date_for(period: PeriodKey, today: date | None = None) -> date:
    today = today or date.today()
    if period == "1m":
        return today - timedelta(days=30)
    if period == "3m":
        return today - timedelta(days=92)
    if period == "ytd":
        return date(today.year, 1, 1)
    if period == "1y":
        return today - timedelta(days=365)
    if period == "3y":
        return today - timedelta(days=365 * 3)
    raise ValueError(f"Unknown period: {period}")


@dataclass(slots=True)
class RankingRow:
    code: str
    name: str
    category: str          # 內部代碼(market / dividend / active / ...)
    category_label: str    # 中文
    last_close: float | None       # 原始收盤(顯示「目前股價」)
    last_change_pct: float | None  # 今日漲跌 %
    period_return_pct: float | None  # 用 adj_close 算
    vs_index_pct: float | None       # 該 ETF 報酬 - TAIEX 報酬


def get_period_base_close(
    session, etf_id: int, period_start: date,
) -> tuple[date, float] | None:
    """報酬基準:**期間起點的前一個交易日**還原收盤(adj_close)。

    Method = Previous Close —— 與 YP-Finance / Yahoo / 大多數金融網站口徑一致。
    若 period_start 之前該 ETF 沒有任何 adj_close(例如新上市),回 None,
    呼叫端會把該 ETF 從排行榜排除。

    範例:
    - YTD 2026:period_start=2026-01-01 → 回 2025-12-31 收盤
    - 近 1 月:period_start=2026-03-27 → 回 2026-03-26 收盤
    - 若 03-26 是週末:自動往前找最後一個有資料的交易日
    """
    row = session.execute(
        select(DailyKBar.date, DailyKBar.adj_close)
        .where(DailyKBar.etf_id == etf_id)
        .where(DailyKBar.date < period_start)
        .where(DailyKBar.adj_close.is_not(None))
        .order_by(DailyKBar.date.desc())
        .limit(1)
    ).first()
    return (row[0], row[1]) if row else None


def apply_d_formula(
    session, etf_id: int, period_start: date,
) -> tuple[date, date, float] | None:
    """D 公式 — 用 adj_close 計算,自動處理股票分割 + 除權息。

    給長期(≥ 1y)報酬計算用,因為 B 公式遇 reverse split (如 0050 2025-06-18 1:4)
    會出現假跌(raw close 跨越分割點計算錯誤)。

    短期(1m/3m/ytd)請繼續用 apply_b_formula(對 YP-Finance 比較貼)。
    """
    base = session.execute(
        select(DailyKBar.date, DailyKBar.adj_close)
        .where(DailyKBar.etf_id == etf_id)
        .where(DailyKBar.date < period_start)
        .where(DailyKBar.adj_close.is_not(None))
        .order_by(DailyKBar.date.desc())
        .limit(1)
    ).first()
    last = session.execute(
        select(DailyKBar.date, DailyKBar.adj_close)
        .where(DailyKBar.etf_id == etf_id)
        .where(DailyKBar.adj_close.is_not(None))
        .order_by(DailyKBar.date.desc())
        .limit(1)
    ).first()
    if not base or not last or base[1] == 0:
        return None
    ret = (last[1] / base[1] - 1) * 100
    return (base[0], last[0], ret)


def get_period_base_close_raw(
    session, etf_id: int, period_start: date,
) -> tuple[date, float] | None:
    """同 get_period_base_close 但回 raw close — 供 B 公式 / 指數使用。"""
    row = session.execute(
        select(DailyKBar.date, DailyKBar.close)
        .where(DailyKBar.etf_id == etf_id)
        .where(DailyKBar.date < period_start)
        .order_by(DailyKBar.date.desc())
        .limit(1)
    ).first()
    return (row[0], row[1]) if row else None


def apply_b_formula(
    session, etf_id: int, period_start: date,
) -> tuple[date, date, float, float] | None:
    """**Total Return / B 公式** — 報酬計算的唯一入口。

    `return = (期末 raw close + 期間累積現金股利) / 期初 raw close - 1`

    - 期初 raw close = period_start **前一個交易日**收盤(Previous Close 法)
    - 期間累積現金股利 = ex_date 在 (base_date, last_date] 區間內的 cash dividends 加總
    - 期末 raw close = DB 內最後一筆 close

    回 (base_date, last_date, return_pct, sum_cash_div) 或 None。
    """
    base = get_period_base_close_raw(session, etf_id, period_start)
    if not base:
        return None
    base_date, base_close = base

    last = session.execute(
        select(DailyKBar.date, DailyKBar.close)
        .where(DailyKBar.etf_id == etf_id)
        .order_by(DailyKBar.date.desc())
        .limit(1)
    ).first()
    if not last or last[1] is None:
        return None
    last_date, last_close = last

    if base_close == 0:
        return None

    div_sum = session.scalar(
        select(func.coalesce(func.sum(Dividend.cash_dividend), 0.0))
        .where(Dividend.etf_id == etf_id)
        .where(Dividend.ex_date > base_date)
        .where(Dividend.ex_date <= last_date)
    ) or 0.0

    ret_pct = (last_close + div_sum) / base_close * 100 - 100
    return (base_date, last_date, ret_pct, float(div_sum))


def _last_close(session, etf_id: int) -> tuple[date, float, float | None] | None:
    """最後一筆 (date, raw_close, adj_close)。raw 用來顯示,adj 用來算報酬。"""
    row = session.execute(
        select(DailyKBar.date, DailyKBar.close, DailyKBar.adj_close)
        .where(DailyKBar.etf_id == etf_id)
        .order_by(DailyKBar.date.desc())
        .limit(1)
    ).first()
    return (row[0], row[1], row[2]) if row else None


def _prev_close(session, etf_id: int, before: date) -> float | None:
    """before 之前最後一個交易日的原始收盤 — 用來算今日漲跌幅。"""
    return session.scalar(
        select(DailyKBar.close)
        .where(DailyKBar.etf_id == etf_id)
        .where(DailyKBar.date < before)
        .order_by(DailyKBar.date.desc())
        .limit(1)
    )


def _index_period_return(session, period_start: date) -> float | None:
    """大盤同期間報酬(Previous Close 法)— TAIEX 用 raw close,指數沒 adj_close。"""
    taiex = session.scalar(select(ETF).where(ETF.code == TAIEX_CODE))
    if not taiex:
        return None
    base = get_period_base_close_raw(session, taiex.id, period_start)
    last = session.execute(
        select(DailyKBar.close)
        .where(DailyKBar.etf_id == taiex.id)
        .order_by(DailyKBar.date.desc())
        .limit(1)
    ).scalar()
    if base is None or last is None or base[1] == 0:
        return None
    return (last / base[1] - 1) * 100


def get_market_overview(today: date | None = None) -> dict:
    """頂部「市場概況」卡片 — 大盤 TAIEX 即時點位 + 1m / 3m / YTD 報酬。

    用一個地方取代每個排行 section 的「對照大盤」,符合 user 要求的統一原則。
    """
    today = today or date.today()
    out: dict = {"taiex_close": None, "taiex_date": None, "returns": {}}

    with session_scope() as session:
        taiex = session.scalar(select(ETF).where(ETF.code == TAIEX_CODE))
        if not taiex:
            return out

        last = session.execute(
            select(DailyKBar.date, DailyKBar.close)
            .where(DailyKBar.etf_id == taiex.id)
            .order_by(DailyKBar.date.desc())
            .limit(1)
        ).first()
        if last:
            out["taiex_date"] = last[0].isoformat()
            out["taiex_close"] = last[1]

        for key in ("1m", "3m", "ytd"):
            ps = _start_date_for(key, today)
            ret = _index_period_return(session, ps)
            out["returns"][key] = {
                "label": PERIOD_LABELS[key],
                "value": ret,
            }
    return out


def get_leverage_ranking(
    period: PeriodKey,
    direction: str,    # "positive" or "inverse"
    *,
    limit: int = 10,
    today: date | None = None,
) -> dict:
    """槓桿/反向獨立排行(高風險區塊用)。direction='positive' 看 2 倍正向,'inverse' 看反向。"""
    from app.services.etf_classifier import leverage_subtype, label_of

    period_start = _start_date_for(period, today)
    today = today or date.today()

    out_rows: list[RankingRow] = []
    with session_scope() as session:
        etfs = session.scalars(
            select(ETF)
            .where(ETF.is_active.is_(True))
            .where(ETF.category == "leverage")
        ).all()

        index_ret = _index_period_return(session, period_start)

        for etf in etfs:
            subtype, _mult = leverage_subtype(etf.code, etf.name)
            if subtype != direction:
                continue
            last = _last_close(session, etf.id)
            if not last:
                continue
            last_date, last_raw_close, _last_adj = last

            prev_raw = _prev_close(session, etf.id, last_date)
            chg_pct = (
                (last_raw_close / prev_raw - 1) * 100
                if prev_raw and prev_raw > 0 else None
            )

            b = apply_b_formula(session, etf.id, period_start)
            period_ret = b[2] if b else None
            if _is_anomalous(period_ret):
                logger.warning(
                    "[ranking] anomaly excluded: %s %s period_ret=%.2f%% (likely split)",
                    etf.code, etf.name, period_ret,
                )
                period_ret = None
            if period_ret is None:
                continue

            vs_index = period_ret - index_ret if index_ret is not None else None
            out_rows.append(RankingRow(
                code=etf.code,
                name=etf.name,
                category=etf.category,
                category_label="槓桿型" if direction == "positive" else "反向型",
                last_close=last_raw_close,
                last_change_pct=chg_pct,
                period_return_pct=period_ret,
                vs_index_pct=vs_index,
            ))

    out_rows.sort(key=lambda r: -(r.period_return_pct or 0))
    out_rows = out_rows[:limit]

    return {
        "category": f"leverage_{direction}",
        "category_label": "槓桿型(2 倍正向)" if direction == "positive" else "反向型(-1 倍)",
        "period": period,
        "period_label": PERIOD_LABELS[period],
        "period_start": period_start.isoformat(),
        "today": today.isoformat(),
        "index_return_pct": index_ret,
        "rows": out_rows,
        "row_count": len(out_rows),
    }


def get_top_movers(
    period: PeriodKey,
    *,
    limit: int = 10,
    today: date | None = None,
    exclude_categories: tuple[str, ...] = ("index", "leverage", "bond"),
) -> dict:
    """跨類別 Top N — 「近月最火」用。預設排除指數本身、槓桿反向、債券。海外仍可上榜。"""
    period_start = _start_date_for(period, today)
    today = today or date.today()

    out_rows: list[RankingRow] = []

    with session_scope() as session:
        etfs = session.scalars(
            select(ETF)
            .where(ETF.is_active.is_(True))
            .where(~ETF.category.in_(exclude_categories))
        ).all()

        index_ret = _index_period_return(session, period_start)

        for etf in etfs:
            last = _last_close(session, etf.id)
            if not last:
                continue
            last_date, last_raw_close, last_adj_close = last

            prev_raw = _prev_close(session, etf.id, last_date)
            chg_pct = (
                (last_raw_close / prev_raw - 1) * 100
                if prev_raw and prev_raw > 0 else None
            )

            b = apply_b_formula(session, etf.id, period_start)
            period_ret = b[2] if b else None
            if _is_anomalous(period_ret):
                logger.warning(
                    "[ranking] anomaly excluded: %s %s period_ret=%.2f%% (likely split)",
                    etf.code, etf.name, period_ret,
                )
                period_ret = None

            if period_ret is None:
                continue

            vs_index = period_ret - index_ret if index_ret is not None else None
            out_rows.append(RankingRow(
                code=etf.code,
                name=etf.name,
                category=etf.category,
                category_label=label_of(etf.category),
                last_close=last_raw_close,
                last_change_pct=chg_pct,
                period_return_pct=period_ret,
                vs_index_pct=vs_index,
            ))

    out_rows.sort(key=lambda r: -(r.period_return_pct or 0))
    out_rows = out_rows[:limit]

    return {
        "category": "all",
        "category_label": "全市場",
        "period": period,
        "period_label": PERIOD_LABELS[period],
        "period_start": period_start.isoformat(),
        "today": today.isoformat(),
        "index_return_pct": index_ret,
        "rows": out_rows,
        "row_count": len(out_rows),
    }


def get_ranking(
    category: str,
    period: PeriodKey,
    *,
    limit: int | None = None,
    today: date | None = None,
) -> dict:
    """傳回 {period, period_label, category, category_label, index_return, rows[]}。"""
    period_start = _start_date_for(period, today)
    today = today or date.today()

    out_rows: list[RankingRow] = []

    with session_scope() as session:
        etfs = session.scalars(
            select(ETF)
            .where(ETF.is_active.is_(True))
            .where(ETF.category == category)
            .order_by(ETF.code.asc())
        ).all()

        index_ret = _index_period_return(session, period_start)

        for etf in etfs:
            last = _last_close(session, etf.id)
            if not last:
                continue
            last_date, last_raw_close, last_adj_close = last

            prev_raw = _prev_close(session, etf.id, last_date)
            chg_pct = (
                (last_raw_close / prev_raw - 1) * 100
                if prev_raw and prev_raw > 0 else None
            )

            b = apply_b_formula(session, etf.id, period_start)
            period_ret = b[2] if b else None
            if _is_anomalous(period_ret):
                logger.warning(
                    "[ranking] anomaly excluded: %s %s period_ret=%.2f%% (likely split)",
                    etf.code, etf.name, period_ret,
                )
                period_ret = None

            vs_index = (
                period_ret - index_ret
                if (period_ret is not None and index_ret is not None)
                else None
            )

            out_rows.append(RankingRow(
                code=etf.code,
                name=etf.name,
                category=etf.category,
                category_label=label_of(etf.category),
                last_close=last_raw_close,
                last_change_pct=chg_pct,
                period_return_pct=period_ret,
                vs_index_pct=vs_index,
            ))

    # 排序:有期間報酬的在前面,按降冪
    out_rows.sort(
        key=lambda r: (r.period_return_pct is None, -(r.period_return_pct or 0)),
    )

    if limit is not None:
        out_rows = out_rows[:limit]

    return {
        "category": category,
        "category_label": label_of(category),
        "period": period,
        "period_label": PERIOD_LABELS[period],
        "period_start": period_start.isoformat(),
        "today": today.isoformat(),
        "index_return_pct": index_ret,
        "rows": out_rows,
        "row_count": len(out_rows),
    }
