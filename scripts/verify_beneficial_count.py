"""驗證受益人數 DB 資料與 FinMind 即時抓的最近 4 週一致。

5 檔抽樣:0050 / 0056 / 00878 / 00981A / 00919
- FinMind 即時 fetch 最近 60 天 → 取每 ETF 最後 4 週 total people
- DB 同樣 5 檔 fetch 最後 4 週 (etf_code, week_date, count)
- 逐筆比對,全對 → PASS,任一不對 → FAIL 列差異

執行:.venv/Scripts/python scripts/verify_beneficial_count.py
"""
from __future__ import annotations

import io
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SAMPLES = ["0050", "0056", "00878", "00981A", "00919"]
LATEST_N = 4


def fetch_finmind_latest(code: str, n: int = LATEST_N) -> list[tuple[date, int]]:
    """從 FinMind 抓最近 60 天,取每週 total level,回最後 n 週 (date, count) 升冪。"""
    from app.services import finmind
    today = date.today()
    start = today - timedelta(days=60)
    rows = finmind.request(
        "TaiwanStockHoldingSharesPer",
        data_id=code,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=today.strftime("%Y-%m-%d"),
    )
    out: dict[date, int] = {}
    for r in rows:
        if r.get("HoldingSharesLevel") != "total":
            continue
        try:
            people = int(r["people"])
        except (KeyError, ValueError, TypeError):
            continue
        if people <= 0:
            continue
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        out[d] = people
    sorted_dates = sorted(out.keys())[-n:]
    return [(d, out[d]) for d in sorted_dates]


def fetch_db_latest(code: str, n: int = LATEST_N) -> list[tuple[date, int]]:
    """從 DB 抓最近 n 週,回 [(date, count)] 升冪。"""
    from sqlalchemy import select, desc
    from app.database import session_scope
    from app.models.etf_beneficial_count import EtfBeneficialCount
    with session_scope() as s:
        rows = s.execute(
            select(EtfBeneficialCount.week_date, EtfBeneficialCount.count)
            .where(EtfBeneficialCount.etf_code == code)
            .order_by(desc(EtfBeneficialCount.week_date))
            .limit(n)
        ).all()
    return sorted([(r.week_date, r.count) for r in rows])


def main():
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)-7s | %(name)s | %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    print("=" * 72)
    print(f"verify_beneficial_count — 5 檔 × 最近 {LATEST_N} 週 vs FinMind 即時")
    print("=" * 72)

    all_ok = True
    diffs: list[tuple[str, date, int, int]] = []  # (code, date, fm, db)

    for code in SAMPLES:
        print(f"\n--- {code} ---")
        try:
            fm = fetch_finmind_latest(code, LATEST_N)
            db = fetch_db_latest(code, LATEST_N)
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            all_ok = False
            continue

        fm_map = dict(fm)
        db_map = dict(db)

        # 對日期 union 比對
        all_dates = sorted(set(fm_map.keys()) | set(db_map.keys()))
        # 只看 FinMind 給的最近 N 週
        fm_dates = sorted(fm_map.keys())[-LATEST_N:]

        for d in fm_dates:
            fm_v = fm_map.get(d)
            db_v = db_map.get(d)
            if db_v is None:
                print(f"  ❌ {d}  FinMind={fm_v:>10,}  DB=MISSING")
                all_ok = False
                diffs.append((code, d, fm_v, None))
            elif fm_v != db_v:
                print(f"  ❌ {d}  FinMind={fm_v:>10,}  DB={db_v:>10,}  Δ={db_v-fm_v:+,}")
                all_ok = False
                diffs.append((code, d, fm_v, db_v))
            else:
                weekday = ["週一","週二","週三","週四","週五","週六","週日"][d.weekday()]
                print(f"  ✓  {d} {weekday}  count={fm_v:>10,}")

    print()
    print("=" * 72)
    if all_ok:
        print("✅ PASS — DB 與 FinMind 即時資料完全一致")
    else:
        print(f"❌ FAIL — {len(diffs)} 筆差異")
        for code, d, fm_v, db_v in diffs:
            print(f"  {code} {d}: FinMind={fm_v}, DB={db_v}")
    print("=" * 72)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
