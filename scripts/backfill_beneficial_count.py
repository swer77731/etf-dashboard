"""一次性 — backfill 受益人數歷史 1 年(全 active ETF)。

執行:
    .venv/Scripts/python scripts/backfill_beneficial_count.py
    .venv/Scripts/python scripts/backfill_beneficial_count.py --weeks 52
    .venv/Scripts/python scripts/backfill_beneficial_count.py --codes 0050,0056

預估執行時間:255 ETF × 1s throttle ≈ 4-5 分鐘。
完成後寫 sync_status source='finmind_beneficial' 一筆紀錄(紀律 #20)。

紀律 #18(quota 禮讓):finmind.request 自動限 50%,backfill 跑完佔小時配額 ~10%。
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=52,
                    help="backfill 週數(default 52 = 1 年)")
    ap.add_argument("--codes", type=str, default=None,
                    help="逗號分隔 code list(default = 全 active ETF)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    # 紀律 #18:httpx INFO 印 token,降到 WARNING(同 main.py)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from app.services import beneficial_count_sync, finmind

    print("=" * 72)
    print(f"backfill_beneficial_count — weeks={args.weeks}")
    print("=" * 72)

    # 配額前檢
    q_before = finmind.check_quota()
    print(f"[quota before] used={q_before.used}/{q_before.limit_hour} "
          f"({q_before.ratio:.1%}) room={q_before.room} level={q_before.level}")

    codes = None
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        print(f"target: {len(codes)} ETF(s) — {codes[:10]}{'...' if len(codes)>10 else ''}")
    else:
        print("target: 全 active ETF(category != 'index')")

    t0 = time.time()
    stats = beneficial_count_sync.backfill_all(weeks=args.weeks, codes=codes)
    elapsed = time.time() - t0

    # 配額後檢
    q_after = finmind.check_quota()
    used = q_after.used - q_before.used

    print()
    print("=" * 72)
    print(f"DONE in {elapsed:.0f}s")
    print("=" * 72)
    print(f"  expected:           {stats['expected']}")
    print(f"  ok (≥1 row):        {stats['ok']}")
    print(f"  no_data:            {stats.get('no_data', 0)}")
    print(f"  no_total_rows:      {stats.get('no_total_rows', 0)}")
    print(f"  fetch_error:        {stats.get('fetch_error', 0)}")
    print(f"  total_rows_written: {stats['total_rows_written']:,}")
    if stats["errors"]:
        print(f"\n  first 5 errors:")
        for e in stats["errors"][:5]:
            print(f"    {e['code']}: {e['error'][:120]}")
    print(f"\n[quota after] used={q_after.used}/{q_after.limit_hour} "
          f"({q_after.ratio:.1%}); this run consumed ~{used} call(s)")


if __name__ == "__main__":
    main()
