"""Migration 018:贊助 / 付費合規基礎建設 — checkout_agreements + user_plans。

Note:user task spec 寫 014_add_billing_tables.py,但 014 已被
014_market_temperature.py 用,實際取下一個可用編號 018。

idempotent:
- table 已存在 → skip create
- 不寫 default row
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect

from app.database import Base, engine
from app.models import billing  # noqa: F401 — 註冊 ORM

logger = logging.getLogger(__name__)

TABLES = ["checkout_agreements", "user_plans"]


def run(dry_run: bool = True) -> dict:
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    missing = [t for t in TABLES if t not in existing]

    if not missing:
        logger.info("[migration:018] all tables exist, skip")
        return {"status": "skipped", "created": []}

    if dry_run:
        print(f"DRY RUN — Migration 018: 缺漏 tables = {missing}")
        return {"status": "dry-run", "created": missing}

    target_tables = [
        Base.metadata.tables[t] for t in missing if t in Base.metadata.tables
    ]
    Base.metadata.create_all(engine, tables=target_tables, checkfirst=True)

    insp_after = inspect(engine)
    after = set(insp_after.get_table_names())
    still = [t for t in missing if t not in after]
    if still:
        raise RuntimeError(f"verify failed: still missing {still}")

    logger.info("[migration:018] created %s", missing)
    return {"status": "ok", "created": missing}


if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)-7s | %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    out = run(dry_run=not args.apply)
    print(f"\nResult: {out}")
