"""月月配試算器後端 — Phase 5 Commit 1.5。

純讀本地 DB(資料主權鐵律 #0)。

公開:
- analyze(codes: list[str], today: date | None = None) -> dict
  純函式入口,給 unit test + endpoint 共用。
- GET /api/monthly-income/analyze?codes=0056,00878,00919

Commit 1.5 改動:
- 「典型配息月份」從 rolling 365d → 過去 3 完整年(today.year-3 ~ -1)
  的穩定模式(門檻:某月在 3 年中 ≥ 2 年配過 → typical;1-2 完整年降一級
  ≥ 1 年即算)。今年同月還沒到 ex_date 不再被誤判成「該月不配」。
- 新增 dividend_pattern("月配/季配/半年配/年配/不規律")依 typical 月數
- 殖利率改用「上一個完整年」(last_full_year = today.year - 1)
- last_year_dividend_months 暫留(指向 typical_dividend_months),
  下個 commit 移除

紀律 #20:沒資料用 None,不編造。上市未滿 1 完整年標 note。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.database import session_scope
from app.models.dividend import Dividend
from app.models.etf import ETF
from app.models.kbar import DailyKBar

router = APIRouter(prefix="/api/monthly-income", tags=["monthly_income"])

MAX_CODES = 10
HISTORY_YEARS = 5
INSUFFICIENT_NOTE = "上市未滿 1 年,資料不足"

# 「典型配息月份」分析窗口:過去 3 完整年(不含當年)
TYPICAL_LOOKBACK_YEARS = 3
# 門檻:該月份在 N 個完整年裡至少配過幾年才算 typical
THRESHOLD_FULL_SAMPLE = 2     # 3 完整年都有資料時用
THRESHOLD_REDUCED_SAMPLE = 1  # 1-2 完整年「樣本少,降一級」


# ──────────────────────────────────────────────────────────────────
# Helpers
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


def _classify_pattern(months: list[int]) -> str | None:
    """配息月份數 → 模式標籤。"""
    if not months:
        return None
    n = len(months)
    if n == 12:
        return "月配"
    if n == 4:
        return "季配"
    if n == 2:
        return "半年配"
    if n == 1:
        return "年配"
    return "不規律"


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


# ──────────────────────────────────────────────────────────────────
# Per-ETF 計算
# ──────────────────────────────────────────────────────────────────

def _per_etf(s, code: str, today: date, candidate_years: list[int],
             last_full_year: int) -> dict:
    code = code.upper()
    etf = s.scalar(select(ETF).where(ETF.code == code))
    if not etf:
        return {"code": code, "error": "ETF not found"}

    earliest = _earliest_kbar(s, etf.id)

    # 「完整年」= ETF 該年 1/1 起就有 K 棒(有完整一年的曝險可比較配息模式)
    # 用 earliest_kbar 比 listed_date 可靠(listed_date 在 etf_universe sync 常被填 placeholder)
    complete_years: list[int] = []
    if earliest is not None:
        complete_years = [y for y in candidate_years if earliest <= date(y, 1, 1)]

    # 沒有任何完整年 → 上市未滿 1 完整年 → insufficient
    if not complete_years:
        return {
            "code": code,
            "name": etf.name,
            "typical_dividend_months": [],
            "last_year_dividend_months": [],   # backward compat alias(下個 commit 移除)
            "dividend_pattern": None,
            "last_full_year": last_full_year,
            "last_full_year_yield_pct": None,
            "last_full_year_total_dividend_per_share": None,
            "note": INSUFFICIENT_NOTE,
            "history": [],
        }

    # 抓 complete_years 範圍內所有配息(distinct ex_date 用 set 處理重複問題)
    range_start = date(min(complete_years), 1, 1)
    range_end = date(max(complete_years), 12, 31)
    rows = s.execute(
        select(Dividend.ex_date, Dividend.cash_dividend)
        .where(Dividend.etf_id == etf.id)
        .where(Dividend.ex_date >= range_start)
        .where(Dividend.ex_date <= range_end)
        .where(Dividend.cash_dividend > 0)
        .order_by(Dividend.ex_date.asc())
    ).all()

    # 每年的月份集合(set 處理 00929 / 00939 重複 ex_date 問題:同月算一次)
    months_by_year: dict[int, set[int]] = {}
    cash_by_year: dict[int, float] = {}
    for ex_date, cash in rows:
        y = ex_date.year
        months_by_year.setdefault(y, set()).add(ex_date.month)
        cash_by_year[y] = cash_by_year.get(y, 0.0) + float(cash)

    # 門檻:3 完整年用 ≥2,1-2 完整年降一級用 ≥1
    n_complete = len(complete_years)
    threshold = THRESHOLD_FULL_SAMPLE if n_complete >= 3 else THRESHOLD_REDUCED_SAMPLE

    typical: list[int] = []
    for m in range(1, 13):
        appeared_years = sum(
            1 for y in complete_years if m in months_by_year.get(y, set())
        )
        if appeared_years >= threshold:
            typical.append(m)

    pattern = _classify_pattern(typical)

    # last_full_year 殖利率(只當該年是完整年才算)
    yield_pct: float | None = None
    total_lfy: float | None = None
    if last_full_year in complete_years:
        start_close = _close_on_or_after(s, etf.id, date(last_full_year, 1, 1))
        total = cash_by_year.get(last_full_year, 0.0)
        if total > 0:
            total_lfy = round(total, 2)
            if start_close and start_close > 0:
                yield_pct = round(total / start_close * 100, 2)

    return {
        "code": code,
        "name": etf.name,
        "typical_dividend_months": typical,
        "last_year_dividend_months": typical,   # backward compat alias
        "dividend_pattern": pattern,
        "last_full_year": last_full_year,
        "last_full_year_yield_pct": yield_pct,
        "last_full_year_total_dividend_per_share": total_lfy,
        "note": None,
        "history": _build_history(s, etf.id, today),
    }


# ──────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────

def analyze(codes: list[str], today: date | None = None) -> dict:
    """組合月月配分析(過去 3 完整年穩定模式)。

    Args:
        codes: 1~10 支 ETF code(caller 已驗 long-form / 上限)
        today: 可選 override(測試用)
    """
    today = today or date.today()
    last_full_year = today.year - 1
    candidate_years = [today.year - i for i in range(TYPICAL_LOOKBACK_YEARS, 0, -1)]
    # = [today.year - 3, today.year - 2, today.year - 1] e.g. [2023, 2024, 2025]

    etfs_result: list[dict] = []
    valid_yields: list[float] = []
    coverage: dict[int, list[str]] = {m: [] for m in range(1, 13)}

    with session_scope() as s:
        for code in codes:
            entry = _per_etf(s, code, today, candidate_years, last_full_year)
            etfs_result.append(entry)

            if "error" in entry:
                continue
            for m in entry["typical_dividend_months"]:
                if entry["code"] not in coverage[m]:
                    coverage[m].append(entry["code"])
            if entry.get("last_full_year_yield_pct") is not None:
                valid_yields.append(entry["last_full_year_yield_pct"])

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
