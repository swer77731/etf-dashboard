"""Migration 016:教學專區 CMS — categories + articles + 4 筆預設分類。

idempotent:
- table 已存在 → skip create
- 4 個預設 category slug 已存在 → skip insert
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, select

from app.database import Base, engine, session_scope
from app.models import learn  # noqa: F401 — 觸發 ORM 註冊到 metadata
from app.models.learn import LearnCategory

logger = logging.getLogger(__name__)

TABLES = ["learn_categories", "learn_articles"]

DEFAULT_CATEGORIES = [
    {"slug": "site-tutorial", "name": "網站教學", "color": "green", "display_order": 1},
    {"slug": "etf-basics", "name": "ETF 小常識", "color": "blue", "display_order": 2},
    {"slug": "investing-mindset", "name": "投資心法與基本功", "color": "amber", "display_order": 3},
    {"slug": "reserved", "name": "預留分類", "color": "purple", "display_order": 4},
]


def run(dry_run: bool = True) -> dict:
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    missing_tables = [t for t in TABLES if t not in existing]

    # 檢查預設 categories 是否已存在
    cats_to_insert = []
    if not missing_tables:
        with session_scope() as session:
            existing_slugs = set(session.scalars(select(LearnCategory.slug)).all())
            cats_to_insert = [
                c for c in DEFAULT_CATEGORIES if c["slug"] not in existing_slugs
            ]
    else:
        cats_to_insert = list(DEFAULT_CATEGORIES)

    if not missing_tables and not cats_to_insert:
        logger.info("[migration:016] tables + categories all present, skip")
        return {"status": "skipped", "created_tables": [], "inserted_categories": 0}

    if dry_run:
        print("=" * 60)
        print("DRY RUN — Migration 016:learn CMS")
        print(f"  缺漏 tables: {missing_tables}")
        print(f"  缺漏 categories: {[c['slug'] for c in cats_to_insert]}")
        return {"status": "dry-run", "created_tables": missing_tables,
                "inserted_categories": len(cats_to_insert)}

    # Create tables
    target_tables = [
        Base.metadata.tables[t] for t in missing_tables if t in Base.metadata.tables
    ]
    if target_tables:
        Base.metadata.create_all(engine, tables=target_tables, checkfirst=True)
        logger.info("[migration:016] created tables: %s", missing_tables)

    # Insert default categories
    if cats_to_insert:
        with session_scope() as session:
            for c in cats_to_insert:
                session.add(LearnCategory(**c))
            logger.info("[migration:016] inserted %d categories", len(cats_to_insert))

    return {
        "status": "ok",
        "created_tables": missing_tables,
        "inserted_categories": len(cats_to_insert),
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
