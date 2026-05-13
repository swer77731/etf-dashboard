"""Migration 020:learn_articles 預設改為登入會員可看(access_level public → login)。

idempotent:
- 只把現有 'public' 的文章轉為 'login'
- 不動 'sponsor' 級
- 已經沒 'public' 文章 → status=skipped
- 之後若需要再寫純公開文章,手動把 access_level 改回 'public'

紀律 #22:套用前算 expected = 'public' 筆數,套用後驗 actual = 'login' 增量。
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.database import engine

logger = logging.getLogger(__name__)


def _has_table(name: str) -> bool:
    return name in inspect(engine).get_table_names()


def run(dry_run: bool = True) -> dict:
    if not _has_table("learn_articles"):
        logger.info("[migration:020] learn_articles missing, skip")
        return {"status": "skipped"}

    with engine.begin() as conn:
        before = conn.execute(text(
            "SELECT COUNT(*) FROM learn_articles "
            "WHERE access_level='public' AND deleted_at IS NULL"
        )).scalar() or 0

    if before == 0:
        logger.info("[migration:020] no public articles, skip")
        return {"status": "skipped"}

    if dry_run:
        print(f"DRY RUN — Migration 020: would convert {before} public → login")
        return {"status": "dry-run", "rows": before}

    with engine.begin() as conn:
        result = conn.execute(text(
            "UPDATE learn_articles SET access_level='login' "
            "WHERE access_level='public' AND deleted_at IS NULL"
        ))
        affected = result.rowcount or 0
        after_public = conn.execute(text(
            "SELECT COUNT(*) FROM learn_articles "
            "WHERE access_level='public' AND deleted_at IS NULL"
        )).scalar() or 0

    if affected != before:
        logger.warning(
            "[migration:020] expected %d but UPDATE affected %d", before, affected
        )
    if after_public != 0:
        raise RuntimeError(
            f"verify failed: still {after_public} public articles after update"
        )

    logger.info("[migration:020] converted %d articles public → login", affected)
    return {"status": "ok", "rows": affected}


if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)-7s | %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    out = run(dry_run=not args.apply)
    print(f"\nResult: {out}")
