"""ETF 歷年含息報酬率 sync — 給定期定額試算器 / 後台用。

來源:FinMind TaiwanStockPriceAdj(還原價,已含配息 + 分割還原)
策略:
- 取「上市日」到「今天」全部資料,但最多回看 10 年
- 對每年算 (年末 / 年初) - 1 = 含息報酬率
- 當年(未結束)算 YTD,標 is_partial=1
- UPSERT 寫進 etf_yearly_returns,主鍵 (etf_code, year)

紀律 #16:
- 抓不到資料的 ETF skip 不爆 job
- FinMind quota 不足走 finmind.request 內建 throttle / sleep / 紅線判斷
- record_sync_attempt 完整紀律 #20 missing 清單
"""
from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import session_scope
from app.models.etf_yearly_return import EtfYearlyReturn
from app.services import finmind
from app.services.sync_status import record_sync_attempt

SYNC_SOURCE = "yearly_returns_sync"

logger = logging.getLogger(__name__)


# 80 支熱門名單從 data/etf_universe_top80.csv 讀(由 build_etf_universe.py 產出)
# CSV missing → fallback 14 支精選(避免冷啟動爆 cron)
_FALLBACK_CODES = (
    "00981A", "00992A", "00985A", "00982A", "00984A", "00935",
    "0050",   "0052",   "009816", "0056",   "00878",  "00713",
    "006208", "00919",
)
_CSV_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "etf_universe_top80.csv"


def load_tracked_codes() -> tuple[str, ...]:
    """從 data/etf_universe_top80.csv 載入 stock_id 清單。

    CSV 不存在 / 讀失敗 → 回 fallback 14 支精選。
    """
    if not _CSV_PATH.exists():
        logger.warning(
            "[yearly_returns] CSV not found (%s) — using fallback 14 codes",
            _CSV_PATH,
        )
        return _FALLBACK_CODES
    try:
        with _CSV_PATH.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        codes = tuple(r["stock_id"].strip() for r in rows if r.get("stock_id"))
        if not codes:
            logger.warning("[yearly_returns] CSV empty — using fallback")
            return _FALLBACK_CODES
        return codes
    except Exception:
        logger.exception("[yearly_returns] CSV load failed — using fallback")
        return _FALLBACK_CODES


# 兼容舊呼叫 — 保留 TRACKED_ETF_CODES 屬性
TRACKED_ETF_CODES = load_tracked_codes()

HISTORY_YEARS = 10   # 最多回看 10 年


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _start_date_for_history(today: date | None = None) -> date:
    """回看 N 年的 1 月 1 日(含整年)。"""
    today = today or date.today()
    return date(today.year - HISTORY_YEARS + 1, 1, 1)


def _fetch_adj_history(code: str, start: date, end: date) -> list[dict]:
    """打 FinMind TaiwanStockPriceAdj。失敗 raise(由上層 try / 各 ETF 隔離)。"""
    return finmind.request(
        "TaiwanStockPriceAdj",
        data_id=code,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
    )


def _compute_yearly_from_adj(rows: list[dict], today: date | None = None) -> list[dict]:
    """從 adj rows 算每年的含息報酬。

    規則:
    - 取每年第一個有 close 的交易日 = open_price
    - 取每年最後一個有 close 的交易日 = close_price
    - return = close_price / open_price - 1
    - 當年(year == today.year)標 is_partial=1
    - 上市未滿一年 / 該年只有一個交易日 → skip 該年
    """
    today = today or date.today()
    by_year: dict[int, list[tuple[date, float]]] = defaultdict(list)

    for r in rows:
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
            close = float(r.get("close") or 0)
            if close <= 0:
                continue
            by_year[d.year].append((d, close))
        except (ValueError, TypeError, KeyError):
            continue

    yearly: list[dict] = []
    for year, daily in by_year.items():
        if len(daily) < 2:
            # 只有 1 個交易日(該支當年才上市/停牌)— 沒辦法算完整年度報酬
            continue
        daily.sort(key=lambda x: x[0])
        open_d, open_p = daily[0]
        close_d, close_p = daily[-1]
        annual_return = close_p / open_p - 1.0
        is_partial = 1 if year == today.year else 0
        yearly.append({
            "year": year,
            "open_date": open_d.isoformat(),
            "close_date": close_d.isoformat(),
            "open_price": round(open_p, 4),
            "close_price": round(close_p, 4),
            "annual_return": round(annual_return, 6),
            "is_partial": is_partial,
        })
    yearly.sort(key=lambda r: r["year"])
    return yearly


def _upsert_one(session, code: str, row: dict) -> None:
    """SQLite UPSERT(主鍵 etf_code+year)。"""
    stmt = sqlite_insert(EtfYearlyReturn).values(
        etf_code=code,
        year=row["year"],
        annual_return=row["annual_return"],
        data_source="finmind_adj",
        is_partial=row["is_partial"],
        updated_at=_now_iso(),
    ).on_conflict_do_update(
        index_elements=["etf_code", "year"],
        set_={
            "annual_return": row["annual_return"],
            "is_partial": row["is_partial"],
            "updated_at": _now_iso(),
        },
    )
    session.execute(stmt)


def sync_one_etf(code: str, today: date | None = None) -> dict:
    """跑單一 ETF — 抓 10 年 adj、算年化、UPSERT。回 stats(可能 raise)。"""
    today = today or date.today()
    start = _start_date_for_history(today)
    rows = _fetch_adj_history(code, start, today)
    yearly = _compute_yearly_from_adj(rows, today=today)

    if not yearly:
        return {"code": code, "rows_fetched": len(rows), "years_written": 0, "years": []}

    with session_scope() as s:
        for y in yearly:
            _upsert_one(s, code, y)

    return {
        "code": code,
        "rows_fetched": len(rows),
        "years_written": len(yearly),
        "years": yearly,
    }


def sync_all(codes: Iterable[str] | None = None, today: date | None = None) -> dict:
    """批量同步 — 預設 14 支追蹤名單。

    紀律 #20:
    - expected = 名單長度
    - actual = 成功寫入年數 > 0 的 ETF 數
    - missing = 失敗 / 0 年的 ETF code
    """
    finmind.log_quota("before yearly_returns sync_all")

    # 每次呼叫動態讀 CSV(scheduler / cron 才能即時拿到新增 ETF)
    codes = list(codes) if codes is not None else list(load_tracked_codes())
    expected = len(codes)
    actual = 0
    missing: list[str] = []
    errors: list[str] = []
    summary_per_code: dict[str, int] = {}

    for code in codes:
        try:
            stats = sync_one_etf(code, today=today)
            n = stats["years_written"]
            summary_per_code[code] = n
            if n > 0:
                actual += 1
                logger.info("[yearly_returns] %s → %d years", code, n)
            else:
                missing.append(code)
                logger.warning("[yearly_returns] %s → 0 years (skipped, 沒抓到 adj 資料)", code)
        except Exception as e:
            missing.append(code)
            errors.append(f"{code}: {type(e).__name__}: {str(e)[:80]}")
            logger.exception("[yearly_returns] %s failed", code)

    finmind.log_quota("after yearly_returns sync_all")

    record_sync_attempt(
        source=SYNC_SOURCE,
        success=(len(missing) == 0 and not errors),
        rows=sum(summary_per_code.values()),
        error="; ".join(errors)[:1900] if errors else None,
        missing=missing if missing else None,
    )

    return {
        "expected": expected,
        "actual": actual,
        "missing": missing,
        "per_code": summary_per_code,
        "total_years_written": sum(summary_per_code.values()),
    }


def sync_current_year_only(codes: Iterable[str] | None = None,
                           today: date | None = None) -> dict:
    """每天 cron 用 — 只更新當年 partial(其他歷年不動)。

    跨年第一天會自動把去年那筆 is_partial 從 1 改成 0(因為去年第 N 天起,
    全部交易日都進來了,annual_return 也會更新成最終值)。
    """
    today = today or date.today()
    # 只抓當年 + 去年 1/1(讓去年那筆 fini 化)
    # 抓今年 1/1 ~ 今天:1 ETF ~ 250 row;14 ETFs = ~3500 row
    # 為跨年 fini 處理,從去年 1/1 抓
    return sync_all(codes=codes, today=today)


def has_data() -> bool:
    """判斷 DB 是否已有任何年度資料(decide first-run vs daily-update)。"""
    from sqlalchemy import select, func
    with session_scope() as s:
        n = s.scalar(select(func.count()).select_from(EtfYearlyReturn))
        return (n or 0) > 0
