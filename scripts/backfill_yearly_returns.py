"""一次性 backfill — 14 支追蹤 ETF 歷年含息報酬寫進 etf_yearly_returns。

用法(專案根目錄跑):
    python scripts/backfill_yearly_returns.py
    或:.venv/Scripts/python scripts/backfill_yearly_returns.py

執行後印出:
- 每支 ETF 抓到幾年
- 跑完 SQL 統計 verification

不必等 cron 半夜跑,馬上就有 DB 資料。重複跑 idempotent(UPSERT)。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保 import 路徑包含專案根
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    # 確保 table 已建好(idempotent)
    from app.database import init_db
    init_db()

    from app.services import yearly_returns_sync

    print("=" * 60)
    print("ETF Yearly Returns — Backfill 14 ETFs")
    print(f"Tracked: {yearly_returns_sync.TRACKED_ETF_CODES}")
    print(f"History years: {yearly_returns_sync.HISTORY_YEARS}")
    print("=" * 60)

    stats = yearly_returns_sync.sync_all()
    print()
    print("=== Per-ETF 寫入年數 ===")
    for code in yearly_returns_sync.TRACKED_ETF_CODES:
        n = stats["per_code"].get(code, 0)
        flag = "OK" if n > 0 else "MISS"
        print(f"  [{flag}] {code:8} → {n} years")

    print()
    print(f"Expected: {stats['expected']}  Actual: {stats['actual']}  "
          f"Total years written: {stats['total_years_written']}")
    if stats["missing"]:
        print(f"Missing: {stats['missing']}")

    # SQL verification
    print()
    print("=== SQL 統計(主鍵驗證)===")
    from sqlalchemy import text
    from app.database import session_scope
    with session_scope() as s:
        rows = s.execute(text("""
            SELECT etf_code, COUNT(*) as years, MIN(year) as min_y, MAX(year) as max_y,
                   SUM(CASE WHEN is_partial=1 THEN 1 ELSE 0 END) as partial_years
            FROM etf_yearly_returns
            GROUP BY etf_code
            ORDER BY years DESC, etf_code
        """)).all()
        print(f"  {'etf_code':<10} {'years':>6} {'min':>6} {'max':>6} {'partial':>8}")
        print(f"  {'-'*10:<10} {'-'*6:>6} {'-'*6:>6} {'-'*6:>6} {'-'*8:>8}")
        for r in rows:
            print(f"  {r.etf_code:<10} {r.years:>6} {r.min_y:>6} {r.max_y:>6} {r.partial_years:>8}")

    # 當年 partial YTD 報酬
    print()
    print("=== 各 ETF 當年 YTD 報酬(is_partial=1)===")
    with session_scope() as s:
        rows = s.execute(text("""
            SELECT etf_code, year, annual_return
            FROM etf_yearly_returns
            WHERE is_partial = 1
            ORDER BY annual_return DESC
        """)).all()
        for r in rows:
            print(f"  {r.etf_code:<10} {r.year}  {r.annual_return:+.2%}")


if __name__ == "__main__":
    main()
