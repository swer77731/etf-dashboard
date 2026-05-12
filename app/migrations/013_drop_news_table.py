"""Migration 013: 廢掉新聞功能 — DROP news table。

Background
==========
2026-05-12 user 決定整個移除新聞功能(/news 頁面 / 首頁最新新聞 / 詳情頁
相關新聞 / news_15min cron / news_sync.py / News model 全砍)。

這支 migration 負責 DB 端:
1. 把 news table 全 row 匯出到 `data/archive/news_dropped_YYYYMMDD_HHMMSS.sqlite`
   (獨立 SQLite 檔,user 隨時可開來看)
2. DROP TABLE news

紀律 #18 / 紀律 #20:
- backup 失敗就不做 DROP,raise
- DROP 失敗有 etf.db 整檔備份(.bak.<ts>)可手動 restore
- idempotent:news table 已不存在 → status='skipped'
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text

from app.config import DATA_DIR
from app.database import engine

logger = logging.getLogger(__name__)

DB_FILE = DATA_DIR / "etf.db"
ARCHIVE_DIR = DATA_DIR / "archive"


def _table_exists(name: str) -> bool:
    insp = inspect(engine)
    return name in insp.get_table_names()


def _row_count() -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM news")).scalar() or 0


def _backup_db_full() -> Path:
    """整 etf.db 備份(restore 失敗用)。"""
    if not DB_FILE.exists():
        raise FileNotFoundError(DB_FILE)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DB_FILE.with_name(f"{DB_FILE.name}.bak.{ts}")
    shutil.copy2(DB_FILE, bak)
    if not bak.exists() or bak.stat().st_size != DB_FILE.stat().st_size:
        bak.unlink(missing_ok=True)
        raise IOError("backup verify failed")
    return bak


def _archive_news_table() -> Path:
    """把 news table 匯出到獨立 sqlite 檔,user 可隨時打開看。"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = ARCHIVE_DIR / f"news_dropped_{ts}.sqlite"

    src = sqlite3.connect(str(DB_FILE))
    dst = sqlite3.connect(str(archive_path))
    try:
        # CREATE TABLE news (相同 schema)
        src.row_factory = sqlite3.Row
        cur = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='news'"
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("news table sql not found in source")
        dst.execute(row["sql"])

        # 連同 index 一起搬(news.url UNIQUE / published_at INDEX)
        idx_rows = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='news' "
            "AND sql IS NOT NULL"
        ).fetchall()
        for ir in idx_rows:
            try:
                dst.execute(ir["sql"])
            except Exception as e:
                # AUTO_INDEX_xxx 系統索引不能手動 CREATE,跳過 OK
                logger.warning("[migration:013] index skipped: %s", e)

        # 全部 row 搬過去
        rows = src.execute("SELECT * FROM news").fetchall()
        if rows:
            cols = rows[0].keys()
            placeholders = ",".join("?" for _ in cols)
            col_list = ",".join(cols)
            dst.executemany(
                f"INSERT INTO news ({col_list}) VALUES ({placeholders})",
                [tuple(r[c] for c in cols) for r in rows],
            )
        dst.commit()
    finally:
        src.close()
        dst.close()

    if not archive_path.exists() or archive_path.stat().st_size == 0:
        raise IOError(f"archive verify failed: {archive_path}")
    return archive_path


def _restore(bak: Path) -> None:
    engine.dispose()
    shutil.copy2(bak, DB_FILE)


def run(dry_run: bool = True) -> dict:
    result = {"status": "unknown", "rows_archived": 0,
              "archive_path": None, "db_backup_path": None}

    if not _table_exists("news"):
        logger.info("[migration:013] news table already dropped, skip")
        result["status"] = "skipped"
        return result

    n_rows = _row_count()
    result["rows_archived"] = n_rows

    if dry_run:
        print("=" * 72)
        print("DRY RUN — Migration 013: DROP news table(廢新聞功能)")
        print("=" * 72)
        print(f"news rows: {n_rows}")
        print(f"DROP target: news table")
        print(f"will archive to: {ARCHIVE_DIR}/news_dropped_<ts>.sqlite")
        print(f"will db-backup to: {DB_FILE}.bak.<ts>")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    # 1. 獨立 sqlite archive(user 可單獨開)
    try:
        archive = _archive_news_table()
        result["archive_path"] = str(archive)
        logger.info("[migration:013] news table archived -> %s (%d rows)",
                    archive, n_rows)
    except Exception as e:
        logger.exception("[migration:013] archive failed")
        raise RuntimeError(f"archive failed, NOT dropping: {e}") from e

    # 2. 整 db 備份(restore safety net)
    try:
        bak = _backup_db_full()
        result["db_backup_path"] = str(bak)
        logger.info("[migration:013] full db backup OK -> %s", bak)
    except Exception as e:
        logger.exception("[migration:013] db backup failed")
        raise RuntimeError(f"db backup failed: {e}") from e

    # 3. DROP TABLE
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE news"))
        if _table_exists("news"):
            raise RuntimeError("news table still exists after DROP")
        result["status"] = "ok"
        logger.info(
            "[migration:013] SUCCESS — news dropped (archived %d rows to %s)",
            n_rows, result["archive_path"],
        )
        return result

    except Exception as e:
        logger.exception("[migration:013] DROP failed, restoring")
        try:
            _restore(bak)
        except Exception as rerr:
            logger.critical(
                "[migration:013] RESTORE FAILED — manual: copy %s -> %s",
                bak, DB_FILE,
            )
            raise RuntimeError(
                f"migration + restore both failed; manual: {bak} -> {DB_FILE}"
            ) from rerr
        result["status"] = "failed-restored"
        raise RuntimeError(f"migration failed but DB restored: {e}") from e


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
