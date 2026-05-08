"""Migration 011: 分享 + 推薦系統 schema(再做)。

Background
==========
2026-05-08 重做分享系統(前一版 ba8b52f 的 migration 009 已 revert)。
prod / local DB 因為 009 跑過,users 已有 ad_free_until / last_share_at /
referral_code 三欄,share_clicks / share_button_clicks 兩表也還在。
本 migration 嚴格 idempotent — 任何欄位 / 表 / 索引存在就 skip,只補缺漏。

紀律
====
- 紀律 #16:Zeabur 部署不會跑 migration → lifespan auto-runner 觸發
- 紀律 #21:User 既有 referral_code 不重新 fill,只補 NULL 的
"""
from __future__ import annotations

import logging
import secrets
import shutil
import string
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.config import DATA_DIR
from app.database import engine

logger = logging.getLogger(__name__)

DB_FILE = DATA_DIR / "etf.db"

_REF_ALPHABET = string.ascii_uppercase + string.digits  # 36^6 = 2.18B


def _gen_ref_code() -> str:
    return "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))


_USERS_NEW_COLS = [
    ("ad_free_until", "ALTER TABLE users ADD COLUMN ad_free_until DATETIME NULL"),
    ("last_share_at", "ALTER TABLE users ADD COLUMN last_share_at DATETIME NULL"),
    ("referral_code", "ALTER TABLE users ADD COLUMN referral_code VARCHAR(8) NULL"),
]

_REFERRAL_INDEX_NAME = "idx_users_referral_code"
_SQL_REFERRAL_INDEX = (
    f"CREATE UNIQUE INDEX {_REFERRAL_INDEX_NAME} ON users (referral_code)"
)

_SQL_SHARE_CLICKS = """\
CREATE TABLE share_clicks (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    referrer_user_id INTEGER NOT NULL,
    visitor_ip_hash VARCHAR(64) NOT NULL,
    user_agent VARCHAR(255),
    is_valid INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (referrer_user_id) REFERENCES users(id)
)"""

_SHARE_CLICKS_INDEXES = [
    ("idx_share_clicks_referrer",
     "CREATE INDEX idx_share_clicks_referrer ON share_clicks (referrer_user_id, created_at)"),
    ("idx_share_clicks_ip",
     "CREATE INDEX idx_share_clicks_ip ON share_clicks (visitor_ip_hash, created_at)"),
    ("idx_share_clicks_valid_at",
     "CREATE INDEX idx_share_clicks_valid_at ON share_clicks (is_valid, created_at)"),
]

_SQL_BUTTON_CLICKS = """\
CREATE TABLE share_button_clicks (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NULL,
    platform VARCHAR(16) NOT NULL,
    page_url VARCHAR(512),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
)"""

_BUTTON_CLICKS_INDEXES = [
    ("idx_share_btn_at",
     "CREATE INDEX idx_share_btn_at ON share_button_clicks (created_at)"),
    ("idx_share_btn_user_at",
     "CREATE INDEX idx_share_btn_user_at ON share_button_clicks (user_id, created_at)"),
    ("idx_share_btn_platform_at",
     "CREATE INDEX idx_share_btn_platform_at ON share_button_clicks (platform, created_at)"),
]


def _table_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": name},
        ).first()
    )


def _column_exists(conn, table: str, col: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return any(r[1] == col for r in rows)


def _index_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:n"),
            {"n": name},
        ).first()
    )


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


def _backfill_ref_codes(conn) -> int:
    rows = conn.execute(
        text("SELECT id FROM users WHERE referral_code IS NULL")
    ).all()
    if not rows:
        return 0
    existing = {
        r[0]
        for r in conn.execute(
            text("SELECT referral_code FROM users WHERE referral_code IS NOT NULL")
        ).all()
    }
    updated = 0
    for (uid,) in rows:
        for _ in range(20):
            code = _gen_ref_code()
            if code not in existing:
                break
        else:
            raise RuntimeError(f"failed to gen unique ref_code for user {uid}")
        conn.execute(
            text("UPDATE users SET referral_code = :c WHERE id = :i"),
            {"c": code, "i": uid},
        )
        existing.add(code)
        updated += 1
    return updated


def run(dry_run: bool = True) -> dict:
    result = {
        "status": "unknown",
        "backup_path": None,
        "users_cols_added": 0,
        "users_index": False,
        "share_clicks_created": False,
        "share_button_clicks_created": False,
        "users_backfilled": 0,
    }

    with engine.connect() as conn:
        missing_cols = [(name, sql) for name, sql in _USERS_NEW_COLS
                        if not _column_exists(conn, "users", name)]
        index_needed = not _index_exists(conn, _REFERRAL_INDEX_NAME)
        clicks_needed = not _table_exists(conn, "share_clicks")
        clicks_indexes_needed = [(n, sql) for n, sql in _SHARE_CLICKS_INDEXES
                                 if not _index_exists(conn, n)]
        btn_needed = not _table_exists(conn, "share_button_clicks")
        btn_indexes_needed = [(n, sql) for n, sql in _BUTTON_CLICKS_INDEXES
                              if not _index_exists(conn, n)]
        backfill_needed = (
            (len(missing_cols) > 0)  # 新加 referral_code col 一定要 backfill
            or bool(conn.execute(
                text("SELECT 1 FROM users WHERE referral_code IS NULL LIMIT 1")
            ).first()) if _column_exists(conn, "users", "referral_code") else True
        )

    nothing_to_do = (
        not missing_cols
        and not index_needed
        and not clicks_needed
        and not clicks_indexes_needed
        and not btn_needed
        and not btn_indexes_needed
        and not backfill_needed
    )
    if nothing_to_do:
        logger.info("[migration:011] all done, skip")
        result["status"] = "skipped"
        return result

    if dry_run:
        print("=" * 72)
        print("DRY RUN -- Migration 011: 分享 + 推薦(idempotent)")
        print("=" * 72)
        print(f"users 缺欄位:            {len(missing_cols)}")
        for n, sql in missing_cols:
            print(f"  + {sql}")
        print(f"users referral_code idx: {'NEED' if index_needed else 'OK'}")
        print(f"share_clicks 表:         {'NEED CREATE' if clicks_needed else 'OK'}")
        print(f"  缺 index:              {[n for n, _ in clicks_indexes_needed]}")
        print(f"share_button_clicks 表:  {'NEED CREATE' if btn_needed else 'OK'}")
        print(f"  缺 index:              {[n for n, _ in btn_indexes_needed]}")
        print(f"backfill referral_code:  {'NEEDED' if backfill_needed else 'OK'}")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    try:
        bak = _backup()
        result["backup_path"] = str(bak)
        logger.info("[migration:011] backup OK -> %s", bak)
    except Exception as e:
        logger.exception("[migration:011] backup failed")
        raise RuntimeError(f"backup failed: {e}") from e

    try:
        with engine.begin() as conn:
            for name, sql in missing_cols:
                logger.info("[migration:011] %s", sql)
                conn.execute(text(sql))
                result["users_cols_added"] += 1

            if index_needed:
                logger.info("[migration:011] %s", _SQL_REFERRAL_INDEX)
                conn.execute(text(_SQL_REFERRAL_INDEX))
                result["users_index"] = True

            if clicks_needed:
                logger.info("[migration:011] CREATE TABLE share_clicks")
                conn.execute(text(_SQL_SHARE_CLICKS))
                result["share_clicks_created"] = True
            for n, sql in clicks_indexes_needed:
                if not _index_exists(conn, n):  # 重新檢查(剛建表時 index 沒有)
                    conn.execute(text(sql))

            if btn_needed:
                logger.info("[migration:011] CREATE TABLE share_button_clicks")
                conn.execute(text(_SQL_BUTTON_CLICKS))
                result["share_button_clicks_created"] = True
            for n, sql in btn_indexes_needed:
                if not _index_exists(conn, n):
                    conn.execute(text(sql))

            n = _backfill_ref_codes(conn)
            result["users_backfilled"] = n
            if n:
                logger.info("[migration:011] backfilled %s users", n)

        # 驗證
        with engine.connect() as conn:
            null_left = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE referral_code IS NULL")
            ).scalar() or 0
            if null_left > 0:
                raise RuntimeError(f"{null_left} users still NULL referral_code after backfill")

        result["status"] = "ok"
        logger.info("[migration:011] SUCCESS")
        return result

    except Exception as e:
        logger.exception("[migration:011] failed, restoring")
        try:
            _restore(bak)
        except Exception as rerr:
            logger.critical(
                "[migration:011] RESTORE FAILED -- manual: copy %s -> %s",
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
