"""一次性 — backfill SITCA AUM 12 個月歷史。

執行:
    .venv/Scripts/python scripts/backfill_aum.py
    .venv/Scripts/python scripts/backfill_aum.py --months 12

預估執行時間:12 GET × ~2s ≈ 30 秒(SITCA 沒 quota 限制)。
完成後寫 sync_status source='sitca_aum_monthly' 一筆紀錄(紀律 #20)。
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
    ap.add_argument("--months", type=int, default=12,
                    help="backfill 月數(default 12)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from app.services import aum_sync

    print("=" * 72)
    print(f"backfill_aum — months={args.months}")
    print("=" * 72)

    t0 = time.time()
    stats = aum_sync.backfill_all_months(months=args.months)
    elapsed = time.time() - t0

    print()
    print("=" * 72)
    print(f"DONE in {elapsed:.0f}s")
    print("=" * 72)
    print(f"  target_months:      {stats['target_months']}")
    print(f"  ok:                 {stats['ok']}")
    print(f"  no_data:            {stats['no_data']}")
    print(f"  degraded:           {stats['degraded']}")
    print(f"  fetch_error:        {stats['fetch_error']}")
    print(f"  total_rows_written: {stats['total_rows_written']:,}")
    print(f"  yms_attempted:      {stats['yms_attempted']}")


if __name__ == "__main__":
    main()
