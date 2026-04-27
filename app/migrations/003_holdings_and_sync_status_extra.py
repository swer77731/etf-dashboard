"""Migration 003: holdings table + sync_status 紀律 #20 三新欄位。

Background
==========
新功能「ETF 持股分析」需要本地 holdings table。
紀律 #20 資料完整性鐵律 → sync_status 加 retry_count / missing_count / missing_items。

Scope
=====
1. CREATE TABLE holdings(若不存在)
2. ALTER TABLE sync_status ADD COLUMN retry_count INTEGER DEFAULT 0(若不存在)
3. ALTER TABLE sync_status ADD COLUMN missing_count INTEGER DEFAULT 0(若不存在)
4. ALTER TABLE sync_status ADD COLUMN missing_items TEXT DEFAULT '[]'(若不存在)

Idempotent
==========
- holdings 已存在 → skip create
- sync_status.retry_count 已存在 → skip alter

Safety
======
backup → idempotent check → execute → verify → restore on fail(同 001 模式)。

執行
====
    python -m app.migrations.003_holdings_and_sync_status_extra          # dry-run
    python -m app.migrations.003_holdings_and_sync_status_extra --apply  # 真執行
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.config import DATA_DIR
from app.database import engine

logger = logging.getLogger(__name__)

DB_FILE = DATA_DIR / "etf.db"


# ──────────────────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────────────────

_SQL_CREATE_HOLDINGS = """\
CREATE TABLE holdings (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    etf_id INTEGER NOT NULL,
    stock_code VARCHAR(16) NOT NULL,
    stock_name VARCHAR(64) NOT NULL,
    weight FLOAT NOT NULL,
    sector VARCHAR(64),
    rank INTEGER NOT NULL,
    updated_at DATETIME NOT NULL,
    source VARCHAR(32),
    FOREIGN KEY (etf_id) REFERENCES etf_list(id) ON DELETE CASCADE
)"""

_SQL_UNIQUE_IDX = (
    "CREATE UNIQUE INDEX uq_holdings_etf_stock_date "
    "ON holdings (etf_id, stock_code, updated_at)"
)
_SQL_INDEX = (
    "CREATE INDEX ix_holdings_etf ON holdings (etf_id)"
)

_SQL_ALTER_RETRY_COUNT = (
    "ALTER TABLE sync_status ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
)
_SQL_ALTER_MISSING_COUNT = (
    "ALTER TABLE sync_status ADD COLUMN missing_count INTEGER NOT NULL DEFAULT 0"
)
_SQL_ALTER_MISSING_ITEMS = (
    "ALTER TABLE sync_status ADD COLUMN missing_items TEXT NOT NULL DEFAULT '[]'"
)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _holdings_exists() -> bool:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='holdings'")
        ).all()
    return len(rows) > 0


def _sync_status_has_retry_count() -> bool:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('sync_status')")).all()
    cols = {r[1] for r in rows}
    return "retry_count" in cols


def _sync_status_has_missing_count() -> bool:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('sync_status')")).all()
    cols = {r[1] for r in rows}
    return "missing_count" in cols


def _sync_status_has_missing_items() -> bool:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('sync_status')")).all()
    cols = {r[1] for r in rows}
    return "missing_items" in cols


def _backup_db() -> Path:
    if not DB_FILE.exists():
        raise FileNotFoundError(f"DB file missing: {DB_FILE}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_FILE.with_name(f"{DB_FILE.name}.bak.{ts}")

    src_size = DB_FILE.stat().st_size
    shutil.copy2(DB_FILE, backup_path)

    if not backup_path.exists():
        raise IOError(f"Backup not created: {backup_path}")
    if backup_path.stat().st_size != src_size:
        backup_path.unlink(missing_ok=True)
        raise IOError("Backup size mismatch — DB may have changed mid-copy")

    logger.info("[migration:003] backup OK → %s", backup_path)
    return backup_path


def _restore_db(backup_path: Path) -> None:
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup gone: {backup_path}")
    engine.dispose()
    shutil.copy2(backup_path, DB_FILE)
    logger.warning("[migration:003] restored from %s", backup_path)


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def _build_pending_sql() -> list[str]:
    """根據當前 schema 狀態決定要跑哪些 SQL(idempotent)。"""
    pending: list[str] = []
    if not _holdings_exists():
        pending.append(_SQL_CREATE_HOLDINGS)
        pending.append(_SQL_UNIQUE_IDX)
        pending.append(_SQL_INDEX)
    if not _sync_status_has_retry_count():
        pending.append(_SQL_ALTER_RETRY_COUNT)
    if not _sync_status_has_missing_count():
        pending.append(_SQL_ALTER_MISSING_COUNT)
    if not _sync_status_has_missing_items():
        pending.append(_SQL_ALTER_MISSING_ITEMS)
    return pending


def run(dry_run: bool = True) -> dict:
    """執行 migration 003。"""
    result = {
        "status": "unknown",
        "backup_path": None,
        "sql_count": 0,
        "skipped_reason": None,
    }

    pending = _build_pending_sql()
    result["sql_count"] = len(pending)

    if not pending:
        logger.info("[migration:003] all changes already applied, skip")
        result["status"] = "skipped"
        result["skipped_reason"] = "all changes already applied"
        return result

    if dry_run:
        ts_demo = datetime.now().strftime("%Y%m%d_%H%M%S")
        print("=" * 72)
        print("DRY RUN — Migration 003: holdings table + sync_status 紀律 #20 三欄")
        print("=" * 72)
        print(f"Pending SQL count: {len(pending)}")
        print(f"Backup target:     {DB_FILE}.bak.{ts_demo} (timestamped each run)")
        print()
        print("--- Pre-check ---")
        print(f"  holdings exists:                {_holdings_exists()}")
        print(f"  sync_status.retry_count:        {_sync_status_has_retry_count()}")
        print(f"  sync_status.missing_count:      {_sync_status_has_missing_count()}")
        print(f"  sync_status.missing_items:      {_sync_status_has_missing_items()}")
        print()
        print("--- SQL to execute ---")
        for i, sql in enumerate(pending, 1):
            print(f"\n[{i}/{len(pending)}]")
            print(sql)
        print()
        print("--- Post-execution checks ---")
        print("  holdings table exists")
        print("  sync_status has all 3 new columns")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    try:
        backup_path = _backup_db()
        result["backup_path"] = str(backup_path)
    except Exception as e:
        logger.exception("[migration:003] BACKUP FAILED — abort")
        raise RuntimeError(f"backup failed, migration NOT started: {e}") from e

    try:
        with engine.begin() as conn:
            for sql in pending:
                logger.info("[migration:003] exec: %s", sql.split("\n", 1)[0][:80])
                conn.execute(text(sql))

        # Verify
        if not _holdings_exists():
            raise RuntimeError("holdings table not found after CREATE")
        if not _sync_status_has_retry_count():
            raise RuntimeError("sync_status.retry_count not found after ALTER")
        if not _sync_status_has_missing_count():
            raise RuntimeError("sync_status.missing_count not found after ALTER")
        if not _sync_status_has_missing_items():
            raise RuntimeError("sync_status.missing_items not found after ALTER")

        result["status"] = "ok"
        logger.info("[migration:003] SUCCESS — all schema changes applied")
        return result

    except Exception as e:
        logger.exception("[migration:003] FAILED, restoring from backup")
        try:
            _restore_db(backup_path)
        except Exception as restore_err:
            logger.critical(
                "[migration:003] RESTORE ALSO FAILED — manual recovery: copy %s → %s",
                backup_path, DB_FILE,
            )
            raise RuntimeError(
                f"migration failed AND restore failed; "
                f"manual recovery: copy {backup_path} → {DB_FILE}"
            ) from restore_err
        result["status"] = "failed-restored"
        raise RuntimeError(f"migration failed but DB restored from backup: {e}") from e


if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)-7s | %(message)s")

    parser = argparse.ArgumentParser(description="Migration 003: holdings + sync_status extras")
    parser.add_argument("--apply", action="store_true",
                        help="Actually run migration (default: dry-run)")
    args = parser.parse_args()

    out = run(dry_run=not args.apply)
    print("\n=== Result ===")
    for k, v in out.items():
        print(f"  {k}: {v}")
