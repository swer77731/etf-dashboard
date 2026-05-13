"""Migration 019:付費牆 + 分享試用 + admin 稽核 DB schema。

idempotent:
- user_plans 加 4 欄位(trial_until / last_share_at / total_share_count / pending_notification)
- referrals 表(訪客點 ref 連結紀錄)
- admin_actions 表(admin 手動開通 / 撤銷稽核)
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.database import Base, engine
from app.models import billing  # noqa: F401

logger = logging.getLogger(__name__)


def _has_column(table: str, col: str) -> bool:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == col for c in insp.get_columns(table))


def _has_table(name: str) -> bool:
    return name in inspect(engine).get_table_names()


# user_plans 4 新欄位
USER_PLAN_COLUMNS = [
    ("trial_until", "TIMESTAMP"),
    ("last_share_at", "TIMESTAMP"),
    ("total_share_count", "BIGINT NOT NULL DEFAULT 0"),
    ("pending_notification", "BOOLEAN NOT NULL DEFAULT 0"),
]


def run(dry_run: bool = True) -> dict:
    actions: list[str] = []

    # 1. user_plans 補欄位
    for col, ddl in USER_PLAN_COLUMNS:
        if not _has_column("user_plans", col):
            actions.append(f"ALTER user_plans ADD {col}")

    # 2. referrals 表
    if not _has_table("referrals"):
        actions.append("CREATE referrals")

    # 3. admin_actions 表
    if not _has_table("admin_actions"):
        actions.append("CREATE admin_actions")

    if not actions:
        logger.info("[migration:019] all up to date, skip")
        return {"status": "skipped"}

    if dry_run:
        print("DRY RUN — Migration 019:")
        for a in actions:
            print(f"  {a}")
        return {"status": "dry-run", "actions": actions}

    # Apply
    # 1. ALTER user_plans
    with engine.begin() as conn:
        for col, ddl in USER_PLAN_COLUMNS:
            if not _has_column("user_plans", col):
                conn.execute(text(f"ALTER TABLE user_plans ADD COLUMN {col} {ddl}"))
                logger.info("[migration:019] user_plans + %s", col)

    # 2/3. CREATE referrals / admin_actions(SQLAlchemy 建,含 indexes)
    to_create = []
    if not _has_table("referrals"):
        to_create.append(Base.metadata.tables["referrals"])
    if not _has_table("admin_actions"):
        to_create.append(Base.metadata.tables["admin_actions"])
    if to_create:
        Base.metadata.create_all(engine, tables=to_create, checkfirst=True)
        for t in to_create:
            logger.info("[migration:019] created table %s", t.name)

    # Verify
    for col, _ in USER_PLAN_COLUMNS:
        if not _has_column("user_plans", col):
            raise RuntimeError(f"verify failed: user_plans missing {col}")
    if not _has_table("referrals"):
        raise RuntimeError("verify failed: referrals")
    if not _has_table("admin_actions"):
        raise RuntimeError("verify failed: admin_actions")

    return {"status": "ok", "actions": actions}


if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)-7s | %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    out = run(dry_run=not args.apply)
    print(f"\nResult: {out}")
