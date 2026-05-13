"""Migration 015:清 5 個 market_temp table 內的週末 row(治本)。

Background
==========
2026-05-13 修 sync_* 服務加 weekday>=5 skip 後,DB 內仍有 ~7 筆舊週末 row
(04/18/19、04/25/26、05/01-03 等),spot_net_yi=0 / fut/opt/breadth 各
欄位含假資料。view filter 雖能 hide(`!= 0`),但 DB 本身留垃圾。

這支 migration:
1. SELECT 5 個 table 所有 date,Python filter weekday >= 5
2. log 找到的週末 row(date / weekday name)
3. DELETE 這些 row(SQLAlchemy 跨 DB 通用,用 IN list)
4. idempotent:0 筆週末 row → status='skipped'

紀律 #18:DELETE 前充分 log,失敗 raise 不繼續。
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select

from app.database import session_scope
from app.models.market_temperature import (  # noqa: F401
    InstitutionalDaily,
    MarginMaintenance,
    MarginShortTotal,
    MarketBreadth,
    SecuritiesLendingDaily,
)

logger = logging.getLogger(__name__)

_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

TABLES = [
    ("margin_maintenance", MarginMaintenance),
    ("market_breadth", MarketBreadth),
    ("margin_short_total", MarginShortTotal),
    ("securities_lending_daily", SecuritiesLendingDaily),
    ("institutional_daily", InstitutionalDaily),
]


def _find_weekend_dates(session, cls) -> list:
    all_dates = list(session.scalars(select(cls.date)))
    # institutional_daily 同 date 多 row(3 法人),用 set 去重
    uniq = sorted(set(all_dates), reverse=True)
    return [d for d in uniq if d.weekday() >= 5]


def run(dry_run: bool = True) -> dict:
    total_found = 0
    total_deleted = 0
    by_table: dict = {}

    with session_scope() as session:
        # Phase 1:SELECT + log
        for name, cls in TABLES:
            wd_dates = _find_weekend_dates(session, cls)
            by_table[name] = wd_dates
            if wd_dates:
                total_found += len(wd_dates)
                logger.info(
                    "[migration:015] %s: 找到 %d 筆週末 row → %s",
                    name, len(wd_dates),
                    [f"{d} ({_WEEKDAY_NAMES[d.weekday()]})" for d in wd_dates],
                )
            else:
                logger.info("[migration:015] %s: 0 筆週末 row", name)

        if total_found == 0:
            logger.info("[migration:015] all tables clean, skip")
            return {"status": "skipped", "found": 0, "deleted": 0}

        if dry_run:
            print("=" * 72)
            print(f"DRY RUN — Migration 015:DELETE {total_found} 筆週末 row")
            for name, dates in by_table.items():
                if dates:
                    print(f"  {name}: {len(dates)} 筆")
            print("=" * 72)
            return {"status": "dry-run", "found": total_found, "deleted": 0}

        # Phase 2:DELETE
        for name, cls in TABLES:
            wd_dates = by_table[name]
            if not wd_dates:
                continue
            result = session.execute(delete(cls).where(cls.date.in_(wd_dates)))
            total_deleted += result.rowcount or 0
            logger.info(
                "[migration:015] %s: DELETE %d row (預期 %d 個 date,"
                "institutional 同 date × 3 法人 = %d row)",
                name, result.rowcount or 0, len(wd_dates),
                len(wd_dates) * (3 if name == "institutional_daily" else 1),
            )

    logger.info(
        "[migration:015] DONE — found %d distinct dates, deleted %d rows",
        total_found, total_deleted,
    )
    return {
        "status": "ok",
        "found": total_found,
        "deleted": total_deleted,
        "by_table": {name: len(dates) for name, dates in by_table.items()},
    }


if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)-7s | %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    out = run(dry_run=not args.apply)
    print("\n=== Result ===")
    for k, v in out.items():
        print(f"  {k}: {v}")
