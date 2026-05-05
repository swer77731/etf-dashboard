"""驗證 etf_aum DB 資料與 SITCA 即時 fetch 一致。

5 檔抽樣:0050 / 0056 / 00878 / 00981A / 00919
- 對 latest 1 個月各抓 SITCA + DB,逐筆比對 aum_thousand_ntd
- 全對 → ✅ PASS,任一不對 → ❌ FAIL 列差異
"""
from __future__ import annotations

import io
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLES = ["0050", "0056", "00878", "00981A", "00919"]


def main():
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)-7s | %(name)s | %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from app.services import aum_sync
    from app.database import session_scope
    from app.models.etf_aum import EtfAum
    from sqlalchemy import select, desc

    # 找 DB 最新月
    with session_scope() as s:
        latest_dt = s.scalar(select(EtfAum.month_date).order_by(desc(EtfAum.month_date)).limit(1))

    if latest_dt is None:
        print("❌ DB 內 etf_aum 為空,先 backfill")
        sys.exit(1)

    ym = latest_dt.strftime("%Y%m")
    print("=" * 72)
    print(f"verify_aum — 5 檔 latest month {ym} vs SITCA 即時")
    print("=" * 72)

    # SITCA 即時抓
    print(f"\nfetching SITCA latest month {ym} ...")
    rows = aum_sync._fetch_month(ym)
    sitca_map = {r["code"]: r["aum_thousand_ntd"] for r in rows}
    print(f"  SITCA 該月共 {len(sitca_map)} ETF")

    # DB 該月抓 5 檔
    with session_scope() as s:
        db_rows = s.execute(
            select(EtfAum.etf_code, EtfAum.aum_thousand_ntd)
            .where(EtfAum.month_date == latest_dt)
            .where(EtfAum.etf_code.in_(SAMPLES))
        ).all()
        db_map = {r.etf_code: r.aum_thousand_ntd for r in db_rows}

    all_ok = True
    print()
    for code in SAMPLES:
        sv = sitca_map.get(code)
        dv = db_map.get(code)
        if sv is None and dv is None:
            print(f"  ⚠ {code:8} both missing")
            all_ok = False
        elif sv is None:
            print(f"  ⚠ {code:8} SITCA missing, DB={dv:,}")
            all_ok = False
        elif dv is None:
            print(f"  ❌ {code:8} SITCA={sv:,}, DB MISSING")
            all_ok = False
        elif sv != dv:
            print(f"  ❌ {code:8} SITCA={sv:,}, DB={dv:,}, Δ={dv-sv:+,}")
            all_ok = False
        else:
            # 顯示成「億」對齊驗收眼睛
            yi = sv / 1e5
            print(f"  ✓  {code:8} {sv:>15,} 千元 ({yi:>8.1f} 億)")

    print()
    print("=" * 72)
    if all_ok:
        print("✅ PASS — DB 與 SITCA 即時資料一致")
    else:
        print("❌ FAIL")
    print("=" * 72)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
