"""Migration 006: error_reports table — 使用者錯誤回報。

Background
==========
2026-05-04 加錯誤回報系統:8 個有資料頁面右下角浮動「回報錯誤」按鈕,
modal 收 description,寫進 error_reports,後台 /admin/error-reports 處理。

Scope
=====
CREATE TABLE error_reports + indexes(若不存在)。
Idempotent:已存在 → skip。
Safety:同 003 / 004 / 005 模式(backup → check → execute → verify → restore)。
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
CREATE TABLE error_reports (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    page_url VARCHAR(512) NOT NULL,
    description TEXT NOT NULL,
    ip_masked VARCHAR(64),
    user_agent VARCHAR(512),
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    handled_at DATETIME,
    handled_note TEXT
)"""

_SQL_INDEXES = [
    # 後台 list — pending tab 按 created_at DESC
    "CREATE INDEX idx_error_reports_status_created "
    "ON error_reports (status, created_at)",
    # rate-limit 查詢 — WHERE ip_masked = ? AND created_at > ?
    "CREATE INDEX idx_error_reports_ip_created "
    "ON error_reports (ip_masked, created_at)",
]


def _exists() -> bool:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master "
                 "WHERE type='table' AND name='error_reports'")
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
    logger.info("[migration:006] backup OK -> %s", bak)
    return bak


def _restore(bak: Path) -> None:
    engine.dispose()
    shutil.copy2(bak, DB_FILE)


def run(dry_run: bool = True) -> dict:
    result = {"status": "unknown", "backup_path": None, "sql_count": 0}

    if _exists():
        logger.info("[migration:006] error_reports already exists, skip")
        result["status"] = "skipped"
        return result

    sql_list = [_SQL_CREATE, *_SQL_INDEXES]
    result["sql_count"] = len(sql_list)

    if dry_run:
        ts_demo = datetime.now().strftime("%Y%m%d_%H%M%S")
        print("=" * 72)
        print("DRY RUN -- Migration 006: error_reports table")
        print("=" * 72)
        print(f"Backup target:    {DB_FILE}.bak.{ts_demo}")
        print(f"Pending SQL:      {len(sql_list)} statements")
        for i, sql in enumerate(sql_list, 1):
            print(f"\n[{i}/{len(sql_list)}]")
            print(sql)
        print("\n--- Post-execution checks ---")
        print("  error_reports table exists")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    try:
        bak = _backup()
        result["backup_path"] = str(bak)
    except Exception as e:
        logger.exception("[migration:006] backup failed")
        raise RuntimeError(f"backup failed: {e}") from e

    try:
        with engine.begin() as conn:
            for sql in sql_list:
                logger.info("[migration:006] exec: %s", sql.split("\n", 1)[0][:80])
                conn.execute(text(sql))
        if not _exists():
            raise RuntimeError("error_reports not found after CREATE")
        result["status"] = "ok"
        logger.info("[migration:006] SUCCESS")
        return result
    except Exception as e:
        logger.exception("[migration:006] failed, restoring")
        try:
            _restore(bak)
        except Exception as rerr:
            logger.critical(
                "[migration:006] RESTORE FAILED -- manual: copy %s -> %s",
                bak, DB_FILE,
            )
            raise RuntimeError(
                f"migration + restore both failed; manual: {bak} -> {DB_FILE}"
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
