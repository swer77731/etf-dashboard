"""Migration 017:learn_articles 加 is_pinned boolean。

允許 admin 把文章「置頂」到列表前面,上限 5 篇(在 router 端檢查)。
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.database import engine

logger = logging.getLogger(__name__)

TABLE = "learn_articles"
COLUMN = "is_pinned"


def _column_exists() -> bool:
    insp = inspect(engine)
    if TABLE not in insp.get_table_names():
        return False
    return any(c["name"] == COLUMN for c in insp.get_columns(TABLE))


def run(dry_run: bool = True) -> dict:
    if _column_exists():
        logger.info("[migration:017] %s.%s already exists, skip", TABLE, COLUMN)
        return {"status": "skipped"}

    if dry_run:
        print(f"DRY RUN — ALTER TABLE {TABLE} ADD COLUMN {COLUMN} BOOLEAN DEFAULT 0")
        return {"status": "dry-run"}

    with engine.begin() as conn:
        conn.execute(text(
            f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} BOOLEAN NOT NULL DEFAULT 0"
        ))

    if not _column_exists():
        raise RuntimeError("verify failed")
    logger.info("[migration:017] added %s.%s", TABLE, COLUMN)
    return {"status": "ok"}


if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)-7s | %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    out = run(dry_run=not args.apply)
    print(f"\nResult: {out}")
