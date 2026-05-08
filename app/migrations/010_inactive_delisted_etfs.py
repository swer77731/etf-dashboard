"""Migration 010: 標記 10 支已下市 ETF is_active=False。

Background
==========
2026-05-08 user 指名清單 — 0054 / 0058 / 0059 / 0060 / 00649 / 00658L /
00659R / 00667 / 00672L / 00677U 全部已下市,FinMind 多年無新 K 棒,
排行榜 / 健檢仍把它們算進去 → 浪費 quota + UI 混亂。

紀律 #21 例外條款:user 明確指名 code 要 inactive → 跳過 TWSE / 發行商
逐家查證(user 自己已驗)。

Idempotent:已 inactive 的 row 不會再被改,UPDATE 自然 no-op。
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

# 2026-05-08 user 指名清單
DELISTED_CODES = [
    "0054", "0058", "0059", "0060", "00649",
    "00658L", "00659R", "00667", "00672L", "00677U",
]


def _count_still_active() -> int:
    """還有多少在指名清單內仍是 is_active=True。"""
    with engine.connect() as conn:
        codes_csv = ",".join(f"'{c}'" for c in DELISTED_CODES)
        return conn.execute(
            text(f"SELECT COUNT(*) FROM etf_list WHERE code IN ({codes_csv}) AND is_active=1")
        ).scalar() or 0


def _backup() -> Path:
    if not DB_FILE.exists():
        raise FileNotFoundError(DB_FILE)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DB_FILE.with_name(f"{DB_FILE.name}.bak.{ts}")
    shutil.copy2(DB_FILE, bak)
    if not bak.exists() or bak.stat().st_size != DB_FILE.stat().st_size:
        bak.unlink(missing_ok=True)
        raise IOError("backup verify failed")
    return bak


def _restore(bak: Path) -> None:
    engine.dispose()
    shutil.copy2(bak, DB_FILE)


def run(dry_run: bool = True) -> dict:
    result = {"status": "unknown", "codes_to_update": [], "rows_updated": 0}

    n_active = _count_still_active()
    if n_active == 0:
        logger.info("[migration:010] all delisted codes already inactive, skip")
        result["status"] = "skipped"
        return result

    # 列出哪些還要改(prod 跟 local 可能進度不同)
    with engine.connect() as conn:
        codes_csv = ",".join(f"'{c}'" for c in DELISTED_CODES)
        rows = conn.execute(
            text(f"SELECT code, name FROM etf_list "
                 f"WHERE code IN ({codes_csv}) AND is_active=1")
        ).all()
    result["codes_to_update"] = [(r[0], r[1]) for r in rows]

    if dry_run:
        print("=" * 72)
        print("DRY RUN — Migration 010: 標記 10 支已下市 ETF inactive")
        print("=" * 72)
        print(f"目前還是 active 的指名 ETF:{n_active}")
        for code, name in result["codes_to_update"]:
            print(f"  - {code} {name}")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    try:
        bak = _backup()
        logger.info("[migration:010] backup OK -> %s", bak)
    except Exception as e:
        logger.exception("[migration:010] backup failed")
        raise RuntimeError(f"backup failed: {e}") from e

    try:
        with engine.begin() as conn:
            params = {f"c{i}": c for i, c in enumerate(DELISTED_CODES)}
            in_clause = ",".join(f":c{i}" for i in range(len(DELISTED_CODES)))
            r = conn.execute(
                text(f"UPDATE etf_list SET is_active=0 "
                     f"WHERE code IN ({in_clause}) AND is_active=1"),
                params,
            )
            result["rows_updated"] = r.rowcount

        # 驗證
        n_left = _count_still_active()
        if n_left > 0:
            raise RuntimeError(f"{n_left} delisted codes still active after UPDATE")

        result["status"] = "ok"
        logger.info(
            "[migration:010] SUCCESS — %s rows set is_active=0",
            result["rows_updated"],
        )
        return result

    except Exception as e:
        logger.exception("[migration:010] failed, restoring")
        try:
            _restore(bak)
        except Exception as rerr:
            logger.critical(
                "[migration:010] RESTORE FAILED — manual: copy %s -> %s",
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
