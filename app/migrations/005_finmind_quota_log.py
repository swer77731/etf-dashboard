"""Migration 005: finmind_quota_log 表 — 自家 FinMind 呼叫量記錄。

Background
==========
user FinMind 方案 6000/hr 但與朋友共用,user 限額 33% = 2000/hr。
原 finmind.py:check_quota() 打 /user_info 拿 GLOBAL 用量(含朋友);
新表 finmind_quota_log 記 OUR 每筆呼叫,可獨立計算過去 60 分鐘自家使
用量。

Scope
=====
CREATE TABLE finmind_quota_log + index(若不存在)。
Idempotent:已存在 → skip。
Safety:同 003 / 004 模式(backup → check → execute → verify → restore)。
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
CREATE TABLE finmind_quota_log (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    called_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    endpoint VARCHAR(64) NOT NULL
)"""

_SQL_INDEX = (
    "CREATE INDEX idx_quota_log_called_at "
    "ON finmind_quota_log (called_at)"
)


def _exists() -> bool:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master "
                 "WHERE type='table' AND name='finmind_quota_log'")
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
    logger.info("[migration:005] backup OK → %s", bak)
    return bak


def _restore(bak: Path) -> None:
    engine.dispose()
    shutil.copy2(bak, DB_FILE)


def run(dry_run: bool = True) -> dict:
    result = {"status": "unknown", "backup_path": None, "sql_count": 0}

    if _exists():
        logger.info("[migration:005] finmind_quota_log already exists, skip")
        result["status"] = "skipped"
        return result

    sql_list = [_SQL_CREATE, _SQL_INDEX]
    result["sql_count"] = len(sql_list)

    if dry_run:
        ts_demo = datetime.now().strftime("%Y%m%d_%H%M%S")
        print("=" * 72)
        print("DRY RUN — Migration 005: finmind_quota_log table")
        print("=" * 72)
        print(f"Backup target:    {DB_FILE}.bak.{ts_demo}")
        print(f"Pending SQL:      {len(sql_list)} statements")
        for i, sql in enumerate(sql_list, 1):
            print(f"\n[{i}/{len(sql_list)}]")
            print(sql)
        print("\n--- Post-execution checks ---")
        print("  finmind_quota_log table exists")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    try:
        bak = _backup()
        result["backup_path"] = str(bak)
    except Exception as e:
        logger.exception("[migration:005] backup failed")
        raise RuntimeError(f"backup failed: {e}") from e

    try:
        with engine.begin() as conn:
            for sql in sql_list:
                logger.info("[migration:005] exec: %s", sql.split("\n", 1)[0][:80])
                conn.execute(text(sql))
        if not _exists():
            raise RuntimeError("finmind_quota_log not found after CREATE")
        result["status"] = "ok"
        logger.info("[migration:005] SUCCESS")
        return result
    except Exception as e:
        logger.exception("[migration:005] failed, restoring")
        try:
            _restore(bak)
        except Exception as rerr:
            logger.critical(
                "[migration:005] RESTORE FAILED — manual: copy %s → %s",
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
