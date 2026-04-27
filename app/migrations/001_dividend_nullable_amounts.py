"""Migration 001: dividend.cash_dividend / stock_dividend → nullable, no default.

Background
==========
TWSE 除權息預告表「待公告金額」row 需要 cash_dividend = NULL 才能寫入。
Plan Q1(2026-04-27 by user):

    NULL = 未公告 / 未知
    0.0  = 真的配 0 元(罕見但理論可能,如下市清算)

UI 對應(寫進註解,給 UI 階段以後別人看):

    cash_dividend IS NULL → 顯示「待公告」灰字
    cash_dividend = 0.0   → 顯示「0 元」

Scope
=====
- cash_dividend:  NOT NULL DEFAULT 0.0 → NULL OK, no default
- stock_dividend: NOT NULL DEFAULT 0.0 → NULL OK, no default
- 其他欄位完全不動(payment_date / announce_date / fiscal_year 已是 NULL OK)

Idempotent
==========
PRAGMA table_info 看到 cash_dividend 已是 nullable → return,可重跑無副作用。

Safety
======
1. 先 backup data/etf.db → data/etf.db.bak.{YYYYMMDD_HHMMSS}
2. backup 失敗 → raise,不開始 schema 操作
3. migration 失敗 → 自動 restore from backup
4. restore 也失敗 → critical log + 提示手動還原路徑
5. 成功後 SELECT count(*) 驗證 row 數無流失

執行
====
    python -m app.migrations.001_dividend_nullable_amounts          # dry-run(預設)
    python -m app.migrations.001_dividend_nullable_amounts --apply  # 真執行
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
# SQL — 表重建法(SQLite 不支援 ALTER COLUMN DROP NOT NULL)
# ──────────────────────────────────────────────────────────────────

# 新表:cash_dividend / stock_dividend 改 nullable + 拿掉 default
_SQL_CREATE_NEW = """\
CREATE TABLE dividend_new (
    id INTEGER NOT NULL PRIMARY KEY,
    etf_id INTEGER NOT NULL,
    ex_date DATE NOT NULL,
    cash_dividend FLOAT,
    stock_dividend FLOAT,
    payment_date DATE,
    announce_date DATE,
    fiscal_year VARCHAR(8),
    FOREIGN KEY (etf_id) REFERENCES etf_list(id) ON DELETE CASCADE
)"""

# 整批複製(明確列欄位,順序與型別對齊,杜絕 SELECT * 隨 schema 漂移)
_SQL_COPY = """\
INSERT INTO dividend_new (
    id, etf_id, ex_date,
    cash_dividend, stock_dividend,
    payment_date, announce_date, fiscal_year
)
SELECT
    id, etf_id, ex_date,
    cash_dividend, stock_dividend,
    payment_date, announce_date, fiscal_year
FROM dividend"""

_SQL_DROP_OLD = "DROP TABLE dividend"
_SQL_RENAME = "ALTER TABLE dividend_new RENAME TO dividend"

# 重建索引(DROP TABLE 會帶走原本兩個索引)
_SQL_CREATE_UNIQUE_IDX = (
    "CREATE UNIQUE INDEX uq_dividend_etf_exdate ON dividend (etf_id, ex_date)"
)
_SQL_CREATE_IDX = (
    "CREATE INDEX ix_dividend_etf_exdate ON dividend (etf_id, ex_date)"
)


def _migration_sql() -> list[str]:
    """所有要執行的 SQL,順序固定。FK pragma 兩端控管。"""
    return [
        "PRAGMA foreign_keys = OFF",
        _SQL_CREATE_NEW,
        _SQL_COPY,
        _SQL_DROP_OLD,
        _SQL_RENAME,
        _SQL_CREATE_UNIQUE_IDX,
        _SQL_CREATE_IDX,
        "PRAGMA foreign_keys = ON",
    ]


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _check_already_nullable() -> bool:
    """PRAGMA 看 cash_dividend / stock_dividend 是否都已 nullable。"""
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('dividend')")).all()
    state = {r[1]: r[3] for r in rows}   # name -> notnull (1 or 0)
    if "cash_dividend" not in state or "stock_dividend" not in state:
        return False
    return state["cash_dividend"] == 0 and state["stock_dividend"] == 0


def _row_count() -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM dividend")).scalar() or 0


def _backup_db() -> Path:
    """copy etf.db → etf.db.bak.{YYYYMMDD_HHMMSS}。失敗 raise。"""
    if not DB_FILE.exists():
        raise FileNotFoundError(f"DB file missing, cannot migrate: {DB_FILE}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_FILE.with_name(f"{DB_FILE.name}.bak.{ts}")

    src_size = DB_FILE.stat().st_size
    shutil.copy2(DB_FILE, backup_path)

    if not backup_path.exists():
        raise IOError(f"Backup file not created: {backup_path}")
    bak_size = backup_path.stat().st_size
    if bak_size != src_size:
        backup_path.unlink(missing_ok=True)
        raise IOError(
            f"Backup size mismatch (src={src_size} bak={bak_size}) — DB may have changed mid-copy"
        )

    logger.info("[migration:001] backup OK → %s (%d bytes)", backup_path, bak_size)
    return backup_path


def _restore_db(backup_path: Path) -> None:
    """從 backup 還原。先 dispose engine 釋放 file handle 再 copy。"""
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup gone, cannot restore: {backup_path}")
    engine.dispose()
    shutil.copy2(backup_path, DB_FILE)
    logger.warning("[migration:001] restored from %s", backup_path)


# ──────────────────────────────────────────────────────────────────
# Main entry
# ──────────────────────────────────────────────────────────────────

def run(dry_run: bool = True) -> dict:
    """執行 migration(預設 dry-run 只 print SQL 不動 DB)。

    Returns:
        {
            'status': 'skipped' | 'dry-run' | 'ok' | 'failed-restored',
            'backup_path': str | None,
            'rows_before': int,
            'rows_after': int | None,
            'sql_count': int,
        }
    """
    result = {
        "status": "unknown",
        "backup_path": None,
        "rows_before": 0,
        "rows_after": None,
        "sql_count": 0,
    }

    # 1. Idempotent
    if _check_already_nullable():
        logger.info("[migration:001] cash_dividend / stock_dividend already nullable, skip")
        result["status"] = "skipped"
        return result

    # 2. Pre-check: row count + collect SQL
    rows_before = _row_count()
    result["rows_before"] = rows_before
    sql_list = _migration_sql()
    result["sql_count"] = len(sql_list)

    # 3. Dry-run mode
    if dry_run:
        ts_demo = datetime.now().strftime("%Y%m%d_%H%M%S")
        print("=" * 72)
        print("DRY RUN — Migration 001: dividend.cash/stock_dividend → nullable")
        print("=" * 72)
        print(f"Rows to migrate:  {rows_before}")
        print(f"Backup target:    {DB_FILE}.bak.{ts_demo}  (timestamped each run)")
        print(f"Idempotent check: PRAGMA cash_dividend.notnull = "
              f"{1 if not _check_already_nullable() else 0} (will run)")
        print()
        print("--- SQL statements (executed sequentially in single transaction) ---")
        for i, sql in enumerate(sql_list, 1):
            print(f"\n[{i}/{len(sql_list)}]")
            print(sql)
        print()
        print("--- Post-execution checks ---")
        print(f"  SELECT COUNT(*) FROM dividend  →  must equal {rows_before}")
        print(f"  PRAGMA table_info('dividend')   →  cash/stock_dividend.notnull must be 0")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    # 4. Real run — backup first
    try:
        backup_path = _backup_db()
        result["backup_path"] = str(backup_path)
    except Exception as e:
        logger.exception("[migration:001] BACKUP FAILED — abort migration")
        raise RuntimeError(f"backup failed, migration NOT started: {e}") from e

    # 5. Apply migration
    try:
        with engine.begin() as conn:
            for sql in sql_list:
                logger.info("[migration:001] exec: %s", sql.split("\n", 1)[0][:80])
                conn.execute(text(sql))

        # 6. Verify
        rows_after = _row_count()
        result["rows_after"] = rows_after

        if rows_after != rows_before:
            raise RuntimeError(
                f"row count mismatch: before={rows_before} after={rows_after}"
            )
        if not _check_already_nullable():
            raise RuntimeError(
                "schema verify failed: cash/stock_dividend still NOT NULL after migration"
            )

        result["status"] = "ok"
        logger.info("[migration:001] SUCCESS — %d rows preserved, schema now nullable", rows_after)
        return result

    except Exception as e:
        logger.exception("[migration:001] migration failed, restoring from backup")
        try:
            _restore_db(backup_path)
        except Exception as restore_err:
            logger.critical(
                "[migration:001] RESTORE ALSO FAILED — manual recovery: copy %s → %s",
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

    parser = argparse.ArgumentParser(description="Migration 001: dividend amounts → nullable")
    parser.add_argument("--apply", action="store_true",
                        help="Actually run migration (default: dry-run)")
    args = parser.parse_args()

    out = run(dry_run=not args.apply)
    print("\n=== Result ===")
    for k, v in out.items():
        print(f"  {k}: {v}")
