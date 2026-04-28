"""月月配試算器後端 — Phase 5 Commit 1。

純讀本地 DB(資料主權鐵律 #0)。

公開:
- analyze(codes: list[str], today: date | None = None) -> dict
  純函式入口,給 unit test + endpoint 共用。
- GET /api/monthly-income/analyze?codes=0056,00878,00919

紀律 #20:沒資料用 None,不編造。上市未滿 1 年標 note,不矇報酬率。
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.database import session_scope
from app.models.dividend import Dividend
from app.models.etf import ETF
from app.models.kbar import DailyKBar

router = APIRouter(prefix="/api/monthly-income", tags=["monthly_income"])

MAX_CODES = 10
ROLLING_WINDOW_DAYS = 365
HISTORY_YEARS = 5
INSUFFICIENT_NOTE = "上市未滿 1 年,資料不足"


# ──────────────────────────────────────────────────────────────────
# Per-ETF 計算
# ──────────────────────────────────────────────────────────────────

def _earliest_kbar(s, etf_id: int) -> date | None:
    return s.scalar(select(func.min(DailyKBar.date)).where(DailyKBar.etf_id == etf_id))


def _close_on_or_after(s, etf_id: int, target_date: date) -> float | None:
    """target_date 當日或之後第一個交易日的 close。"""
    row = s.execute(
        select(DailyKBar.close)
        .where(DailyKBar.etf_id == etf_id)
        .where(DailyKBar.date >= target_date)
        .order_by(DailyKBar.date.asc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _close_on_or_before(s, etf_id: int, target_date: date) -> float | None:
    row = s.execute(
        select(DailyKBar.close)
        .where(DailyKBar.etf_id == etf_id)
        .where(DailyKBar.date <= target_date)
        .order_by(DailyKBar.date.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _build_history(s, etf_id: int, today: date, years: int = HISTORY_YEARS) -> list[dict]:
    """過去 N 年配息歷史(by year),不含當年。

    每年 yield 估算:該年最後一個交易日 close 當分母。DB 沒到該年就 yield_pct=None。
    """
    cutoff = date(today.year - years, 1, 1)
    end_excl = date(today.year, 1, 1)   # 排除當年
    rows = s.execute(
        select(Dividend.ex_date, Dividend.cash_dividend)
        .where(Dividend.etf_id == etf_id)
        .where(Dividend.ex_date >= cutoff)
        .where(Dividend.ex_date < end_excl)
        .where(Dividend.cash_dividend > 0)
        .order_by(Dividend.ex_date.asc())
    ).all()

    by_year: dict[int, dict] = {}
    for ex_date, cash in rows:
        y = ex_date.year
        bucket = by_year.setdefault(y, {"months": set(), "total": 0.0})
        bucket["months"].add(ex_date.month)
        bucket["total"] += float(cash)

    out: list[dict] = []
    for y in sorted(by_year.keys(), reverse=True):
        info = by_year[y]
        year_close = _close_on_or_before(s, etf_id, date(y, 12, 31))
        yp = (
            round(info["total"] / year_close * 100, 2)
            if year_close and year_close > 0 else None
        )
        out.append({
            "year": y,
            "months": sorted(info["months"]),
            "yield_pct": yp,
            "total": round(info["total"], 2),
        })
    return out


def _per_etf(s, code: str, today: date, period_start: date) -> dict:
    code = code.upper()
    etf = s.scalar(select(ETF).where(ETF.code == code))
    if not etf:
        return {"code": code, "error": "ETF not found"}

    earliest = _earliest_kbar(s, etf.id)

    # 上市未滿 1 年:DB 內最早 K 棒晚於 period_start(滾動 12 個月起點)
    # 用 earliest_kbar 比 listed_date 可靠(listed_date 在 etf_universe sync 常被填 placeholder)
    insufficient = earliest is None or earliest > period_start

    if insufficient:
        return {
            "code": code,
            "name": etf.name,
            "last_year_dividend_months": [],
            "last_year_yield_pct": None,
            "last_year_total_dividend_per_share": None,
            "note": INSUFFICIENT_NOTE,
            "history": [],
        }

    # 滾動 1 年配息(ex_date in (period_start, today])
    rows = s.execute(
        select(Dividend.ex_date, Dividend.cash_dividend)
        .where(Dividend.etf_id == etf.id)
        .where(Dividend.ex_date > period_start)
        .where(Dividend.ex_date <= today)
        .where(Dividend.cash_dividend > 0)
        .order_by(Dividend.ex_date.asc())
    ).all()

    months = sorted({d.month for d, _ in rows})
    total_div = round(sum(float(c) for _, c in rows), 4)

    # 期間起始日 close — period_start 當日或之後第一個交易日
    start_close = _close_on_or_after(s, etf.id, period_start)

    yield_pct: float | None = None
    if start_close and start_close > 0 and total_div > 0:
        yield_pct = round(total_div / start_close * 100, 2)

    return {
        "code": code,
        "name": etf.name,
        "last_year_dividend_months": months,
        "last_year_yield_pct": yield_pct,
        "last_year_total_dividend_per_share": (
            round(total_div, 2) if total_div > 0 else None
        ),
        "note": None,
        "history": _build_history(s, etf.id, today),
    }


# ──────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────

def analyze(codes: list[str], today: date | None = None) -> dict:
    """組合月月配分析。

    Args:
        codes: 1~10 支 ETF code(caller 已驗 long-form / 上限)
        today: 可選 override(測試用)
    """
    today = today or date.today()
    period_start = today - timedelta(days=ROLLING_WINDOW_DAYS)

    etfs_result: list[dict] = []
    valid_yields: list[float] = []
    coverage: dict[int, list[str]] = {m: [] for m in range(1, 13)}

    with session_scope() as s:
        for code in codes:
            entry = _per_etf(s, code, today, period_start)
            etfs_result.append(entry)

            if "error" in entry:
                continue
            for m in entry["last_year_dividend_months"]:
                if entry["code"] not in coverage[m]:
                    coverage[m].append(entry["code"])
            if entry["last_year_yield_pct"] is not None:
                valid_yields.append(entry["last_year_yield_pct"])

    month_coverage = {str(m): cs for m, cs in coverage.items() if cs}
    uncovered_months = [m for m in range(1, 13) if not coverage[m]]
    weighted_avg = (
        round(sum(valid_yields) / len(valid_yields), 2)
        if valid_yields else None
    )

    return {
        "etfs": etfs_result,
        "month_coverage": month_coverage,
        "uncovered_months": uncovered_months,
        "fully_covered": len(uncovered_months) == 0,
        "weighted_avg_yield_pct": weighted_avg,
    }


@router.get("/analyze")
async def analyze_endpoint(codes: str = Query(..., description="逗號分隔 ETF code,最多 10 支")) -> dict:
    """GET /api/monthly-income/analyze?codes=0056,00878,00919"""
    code_list = [c.strip().upper() for c in codes.split(",") if c.strip()]
    if not code_list:
        raise HTTPException(400, "至少需要 1 支 ETF code")
    if len(code_list) > MAX_CODES:
        raise HTTPException(400, f"最多 {MAX_CODES} 支 ETF")
    return analyze(code_list)
