"""Migration 004: holdings_change 表 — 持股變動 (近 10 日 buy/sell/new)。

Background
==========
CMoney holdings API 一次回 10 天的持股 snapshot,可比對首尾算變動。
holdings_change 表存「近 10 日 vs 最舊日」的個股增減。

Scope
=====
CREATE TABLE holdings_change(若不存在)。

Idempotent:已存在 → skip。

Safety:同 001 / 003 模式(backup → check → execute → verify → restore on fail)。
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


_SQL_CREATE = """\
CREATE TABLE holdings_change (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    etf_id INTEGER NOT NULL,
    stock_code VARCHAR(16) NOT NULL,
    stock_name VARCHAR(64) NOT NULL,
    change_direction VARCHAR(8) NOT NULL,
    shares_diff BIGINT NOT NULL,
    weight_latest FLOAT,
    latest_date DATE NOT NULL,
    previous_date DATE NOT NULL,
    updated_at DATETIME NOT NULL,
    source VARCHAR(32),
    FOREIGN KEY (etf_id) REFERENCES etf_list(id) ON DELETE CASCADE
)"""

_SQL_UNIQUE_IDX = (
    "CREATE UNIQUE INDEX uq_holdings_change "
    "ON holdings_change (etf_id, stock_code, updated_at)"
)
_SQL_INDEX_ETF = "CREATE INDEX ix_change_etf ON holdings_change (etf_id)"
_SQL_INDEX_DIR = "CREATE INDEX ix_change_dir ON holdings_change (etf_id, change_direction)"


def _exists() -> bool:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='holdings_change'")
        ).all()
    return len(rows) > 0


def _backup() -> Path:
    if not DB_FILE.exists():
        raise FileNotFoundError(DB_FILE)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DB_FILE.with_name(f"{DB_FILE.name}.bak.{ts}")
    src_size = DB_FILE.stat().st_size
    shutil.copy2(DB_FILE, bak)
    if not bak.exists() or bak.stat().st_size != src_size:
        bak.unlink(missing_ok=True)
        raise IOError("backup verify failed")
    logger.info("[migration:004] backup OK → %s", bak)
    return bak


def _restore(bak: Path) -> None:
    engine.dispose()
    shutil.copy2(bak, DB_FILE)


def run(dry_run: bool = True) -> dict:
    result = {"status": "unknown", "backup_path": None, "sql_count": 0}

    if _exists():
        logger.info("[migration:004] holdings_change already exists, skip")
        result["status"] = "skipped"
        return result

    sql_list = [_SQL_CREATE, _SQL_UNIQUE_IDX, _SQL_INDEX_ETF, _SQL_INDEX_DIR]
    result["sql_count"] = len(sql_list)

    if dry_run:
        ts_demo = datetime.now().strftime("%Y%m%d_%H%M%S")
        print("=" * 72)
        print("DRY RUN — Migration 004: holdings_change table")
        print("=" * 72)
        print(f"Backup target:    {DB_FILE}.bak.{ts_demo}")
        print(f"Pending SQL:      {len(sql_list)} statements")
        for i, sql in enumerate(sql_list, 1):
            print(f"\n[{i}/{len(sql_list)}]")
            print(sql)
        print("\n--- Post-execution checks ---")
        print("  holdings_change table exists")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    try:
        bak = _backup()
        result["backup_path"] = str(bak)
    except Exception as e:
        logger.exception("[migration:004] backup failed")
        raise RuntimeError(f"backup failed: {e}") from e

    try:
        with engine.begin() as conn:
            for sql in sql_list:
                logger.info("[migration:004] exec: %s", sql.split("\n", 1)[0][:80])
                conn.execute(text(sql))
        if not _exists():
            raise RuntimeError("holdings_change not found after CREATE")
        result["status"] = "ok"
        logger.info("[migration:004] SUCCESS")
        return result
    except Exception as e:
        logger.exception("[migration:004] failed, restoring")
        try:
            _restore(bak)
        except Exception as rerr:
            logger.critical(
                "[migration:004] RESTORE FAILED — manual: copy %s → %s",
                bak, DB_FILE,
            )
            raise RuntimeError(
                f"migration + restore both failed; manual: {bak} → {DB_FILE}"
            ) from rerr
        result["status"] = "failed-restored"
        raise RuntimeError(f"migration failed but DB restored: {e}") from e


if __name__ == "__main__":
    import argparse, logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)-7s | %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    out = run(dry_run=not args.apply)
    print("\n=== Result ===")
    for k, v in out.items():
        print(f"  {k}: {v}")
