"""Migration 014:市場溫度計 5 個 table。

新建:
- margin_maintenance(大盤融資維持率)
- market_breadth(漲跌家數)
- margin_short_total(融資+融券大盤合計)
- securities_lending_daily(借券當日交易)
- institutional_daily(三大法人寬表)

紀律 #18:idempotent — table 已存在 → skip。
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect

from app.database import Base, engine
# 觸發 ORM 註冊到 Base.metadata
from app.models import market_temperature  # noqa: F401

logger = logging.getLogger(__name__)

TABLES = [
    "margin_maintenance",
    "market_breadth",
    "margin_short_total",
    "securities_lending_daily",
    "institutional_daily",
]


def run(dry_run: bool = True) -> dict:
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    missing = [t for t in TABLES if t not in existing]

    if not missing:
        logger.info("[migration:014] all tables exist, skip")
        return {"status": "skipped", "created": []}

    if dry_run:
        print("=" * 60)
        print("DRY RUN — Migration 014:市場溫度計 5 tables")
        print(f"  缺漏 tables: {missing}")
        print("=" * 60)
        return {"status": "dry-run", "created": missing}

    # 只 create 缺漏的 table(其他 schema 沒動)
    target_tables = [
        Base.metadata.tables[t] for t in missing if t in Base.metadata.tables
    ]
    Base.metadata.create_all(engine, tables=target_tables, checkfirst=True)

    # verify
    insp_after = inspect(engine)
    after = set(insp_after.get_table_names())
    still_missing = [t for t in missing if t not in after]
    if still_missing:
        raise RuntimeError(f"[migration:014] still missing after create: {still_missing}")

    logger.info("[migration:014] created tables: %s", missing)
    return {"status": "ok", "created": missing}


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
