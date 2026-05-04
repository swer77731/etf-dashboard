"""Migration 007: etf_beneficial_count + etf_aum tables — ETF 健康度系統。

Background
==========
2026-05-04 加 ETF 健康度系統。前台 ETF 詳情頁顯示 2 個指標:
  - 受益人數(週更,FinMind TaiwanStockHoldingSharesPer)
  - 規模(月更,SITCA etf_statement2.aspx?txtYM=YYYYMM&txtR1=0)
1 年歷史。Phase 0 確認 SITCA 單一 GET 拿全 25 欄,但僅存「基金規模(台幣)」
+「總受益人數」對應的核心 2 指標(本 migration 只建 AUM 表;受益人數仍走
FinMind 週粒度,SITCA 月粒度的 holders 不存)。

Scope
=====
CREATE TABLE etf_beneficial_count + etf_aum + indexes(若不存在)。
Idempotent:已存在 → skip。Safety:同 003 / 004 / 005 / 006 模式。
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


_SQL_CREATE_BENEFICIAL = """\
CREATE TABLE etf_beneficial_count (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    etf_code VARCHAR(16) NOT NULL,
    week_date DATE NOT NULL,
    count INTEGER NOT NULL,
    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

_SQL_CREATE_AUM = """\
CREATE TABLE etf_aum (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    etf_code VARCHAR(16) NOT NULL,
    month_date DATE NOT NULL,
    aum_thousand_ntd INTEGER NOT NULL,
    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

_SQL_INDEXES = [
    # etf_beneficial_count: 詳情頁查最新 N 週 + idempotent upsert key
    "CREATE UNIQUE INDEX uq_beneficial_etf_week "
    "ON etf_beneficial_count (etf_code, week_date)",
    "CREATE INDEX idx_beneficial_etf_week_desc "
    "ON etf_beneficial_count (etf_code, week_date DESC)",
    # etf_aum: 同樣兩個 index
    "CREATE UNIQUE INDEX uq_aum_etf_month "
    "ON etf_aum (etf_code, month_date)",
    "CREATE INDEX idx_aum_etf_month_desc "
    "ON etf_aum (etf_code, month_date DESC)",
]


def _exists(table: str) -> bool:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master "
                 "WHERE type='table' AND name=:t"),
            {"t": table},
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
    logger.info("[migration:007] backup OK -> %s", bak)
    return bak


def _restore(bak: Path) -> None:
    engine.dispose()
    shutil.copy2(bak, DB_FILE)


def run(dry_run: bool = True) -> dict:
    result = {"status": "unknown", "backup_path": None, "sql_count": 0}

    bnf_exists = _exists("etf_beneficial_count")
    aum_exists = _exists("etf_aum")
    if bnf_exists and aum_exists:
        logger.info("[migration:007] both tables already exist, skip")
        result["status"] = "skipped"
        return result

    sql_list = []
    if not bnf_exists:
        sql_list.append(_SQL_CREATE_BENEFICIAL)
        sql_list.append(_SQL_INDEXES[0])
        sql_list.append(_SQL_INDEXES[1])
    if not aum_exists:
        sql_list.append(_SQL_CREATE_AUM)
        sql_list.append(_SQL_INDEXES[2])
        sql_list.append(_SQL_INDEXES[3])
    result["sql_count"] = len(sql_list)

    if dry_run:
        ts_demo = datetime.now().strftime("%Y%m%d_%H%M%S")
        print("=" * 72)
        print("DRY RUN -- Migration 007: etf_beneficial_count + etf_aum")
        print("=" * 72)
        print(f"Backup target:    {DB_FILE}.bak.{ts_demo}")
        print(f"Pending SQL:      {len(sql_list)} statements")
        for i, sql in enumerate(sql_list, 1):
            print(f"\n[{i}/{len(sql_list)}]")
            print(sql)
        print("\n--- Post-execution checks ---")
        print("  etf_beneficial_count table exists")
        print("  etf_aum table exists")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    try:
        bak = _backup()
        result["backup_path"] = str(bak)
    except Exception as e:
        logger.exception("[migration:007] backup failed")
        raise RuntimeError(f"backup failed: {e}") from e

    try:
        with engine.begin() as conn:
            for sql in sql_list:
                logger.info("[migration:007] exec: %s", sql.split("\n", 1)[0][:80])
                conn.execute(text(sql))
        if not _exists("etf_beneficial_count") or not _exists("etf_aum"):
            raise RuntimeError("table(s) not found after CREATE")
        result["status"] = "ok"
        logger.info("[migration:007] SUCCESS")
        return result
    except Exception as e:
        logger.exception("[migration:007] failed, restoring")
        try:
            _restore(bak)
        except Exception as rerr:
            logger.critical(
                "[migration:007] RESTORE FAILED -- manual: copy %s -> %s",
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
