"""Migration 012: 再標 8 支 audit 偵測為下市的 ETF inactive。

Background
==========
2026-05-08 全自動資料健檢(`data_audit.etf_likely_delisted`,90 天無 K 棒
門檻)偵測出 9 支候選下市,user 親自審查後決定:

✅ 標 inactive 的 8 支(最後 K 棒 ≥ 1000 天前):
  - 00732   國泰RMB短期報酬          1114 天
  - 00742   新光內需收益              1451 天
  - 00743   國泰中國A150             1318 天
  - 00774B  新光中國政金綠債          1179 天
  - 00774C  新光中政金綠債+R          1260 天
  - 008201  BP 上證 50                1367 天
  - 00866   新光 Shiller CAPE        1947 天
  - 00906   大華元宇宙科技50          1038 天

⏸️  保留觀察(門檻內):
  - 00925   新光標普電動車           338 天(< 1000 天)

紀律 #21 例外條款:user 明確指名跳過 TWSE / 發行商查證(user 自驗)。
Idempotent:已 inactive 不會重改。
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

DELISTED_CODES = [
    "00732", "00742", "00743", "00774B", "00774C",
    "008201", "00866", "00906",
]


def _count_still_active() -> int:
    with engine.connect() as conn:
        codes_csv = ",".join(f"'{c}'" for c in DELISTED_CODES)
        return conn.execute(
            text(f"SELECT COUNT(*) FROM etf_list "
                 f"WHERE code IN ({codes_csv}) AND is_active=1")
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
        logger.info("[migration:012] all 8 codes already inactive, skip")
        result["status"] = "skipped"
        return result

    with engine.connect() as conn:
        codes_csv = ",".join(f"'{c}'" for c in DELISTED_CODES)
        rows = conn.execute(
            text(f"SELECT code, name FROM etf_list "
                 f"WHERE code IN ({codes_csv}) AND is_active=1")
        ).all()
    result["codes_to_update"] = [(r[0], r[1]) for r in rows]

    if dry_run:
        print("=" * 72)
        print("DRY RUN — Migration 012: 標 8 支 audit 偵測下市 ETF inactive")
        print("=" * 72)
        print(f"目前還是 active 的指名 ETF:{n_active}")
        for code, name in result["codes_to_update"]:
            print(f"  - {code} {name}")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    try:
        bak = _backup()
        logger.info("[migration:012] backup OK -> %s", bak)
    except Exception as e:
        logger.exception("[migration:012] backup failed")
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

        n_left = _count_still_active()
        if n_left > 0:
            raise RuntimeError(f"{n_left} delisted codes still active after UPDATE")

        result["status"] = "ok"
        logger.info(
            "[migration:012] SUCCESS — %s rows set is_active=0",
            result["rows_updated"],
        )
        return result

    except Exception as e:
        logger.exception("[migration:012] failed, restoring")
        try:
            _restore(bak)
        except Exception as rerr:
            logger.critical(
                "[migration:012] RESTORE FAILED — manual: copy %s -> %s",
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
