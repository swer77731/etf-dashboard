"""Migration 009: 分享 + 推薦系統 schema。

Background
==========
2026-05-08 加分享按鈕(FB/LINE/Threads/複製)+ 預埋 ad_free 機制。
- users 加 3 欄(ad_free_until, last_share_at, referral_code)
- 新建 share_clicks 表(?ref=XXX 訪客記錄)
- 新建 share_button_clicks 表(分享按鈕點擊記錄)
- 既有 users 全部 backfill referral_code(6 字元 [A-Z0-9])

Idempotent:已存在 → skip。Safety:同 008 模式(備份 + restore)。
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


_SQL_USERS_ADD_COLS = [
    "ALTER TABLE users ADD COLUMN ad_free_until DATETIME NULL",
    "ALTER TABLE users ADD COLUMN last_share_at DATETIME NULL",
    "ALTER TABLE users ADD COLUMN referral_code VARCHAR(8) NULL",
]

_SQL_USERS_INDEX = (
    "CREATE UNIQUE INDEX idx_users_referral_code ON users (referral_code)"
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

_SQL_SHARE_CLICKS_INDEXES = [
    "CREATE INDEX idx_share_clicks_referrer ON share_clicks (referrer_user_id, created_at)",
    "CREATE INDEX idx_share_clicks_ip ON share_clicks (visitor_ip_hash, created_at)",
    "CREATE INDEX idx_share_clicks_valid_at ON share_clicks (is_valid, created_at)",
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

_SQL_BUTTON_CLICKS_INDEXES = [
    "CREATE INDEX idx_share_btn_at ON share_button_clicks (created_at)",
    "CREATE INDEX idx_share_btn_user_at ON share_button_clicks (user_id, created_at)",
    "CREATE INDEX idx_share_btn_platform_at ON share_button_clicks (platform, created_at)",
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
    src_size = DB_FILE.stat().st_size
    shutil.copy2(DB_FILE, bak)
    if not bak.exists() or bak.stat().st_size != src_size:
        bak.unlink(missing_ok=True)
        raise IOError("backup verify failed")
    logger.info("[migration:009] backup OK -> %s", bak)
    return bak


def _restore(bak: Path) -> None:
    engine.dispose()
    shutil.copy2(bak, DB_FILE)


def _backfill_ref_codes(conn) -> int:
    """既有 users.referral_code IS NULL 的全部 fill。"""
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
        for _ in range(20):  # 重試 20 次避碰撞(6 字元 36^6 撞機率近 0)
            code = _gen_ref_code()
            if code not in existing:
                break
        else:
            raise RuntimeError(f"無法產生不撞碼的 referral_code(uid={uid})")
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
        users_missing_cols = [
            sql for sql in _SQL_USERS_ADD_COLS
            if not _column_exists(conn, "users", sql.split("ADD COLUMN ")[1].split()[0])
        ]
        users_index_needed = not _index_exists(conn, "idx_users_referral_code")
        share_clicks_needed = not _table_exists(conn, "share_clicks")
        share_btn_needed = not _table_exists(conn, "share_button_clicks")
        any_user_needs_backfill = bool(
            conn.execute(
                text("SELECT 1 FROM users WHERE referral_code IS NULL LIMIT 1")
            ).first()
        ) if not users_missing_cols else True

    nothing_to_do = (
        not users_missing_cols
        and not users_index_needed
        and not share_clicks_needed
        and not share_btn_needed
        and not any_user_needs_backfill
    )
    if nothing_to_do:
        logger.info("[migration:009] all done, skip")
        result["status"] = "skipped"
        return result

    if dry_run:
        print("=" * 72)
        print("DRY RUN -- Migration 009: 分享 + 推薦系統")
        print("=" * 72)
        print(f"users 缺欄位:            {len(users_missing_cols)}")
        for sql in users_missing_cols:
            print(f"  + {sql}")
        print(f"users referral_code idx: {'NEED' if users_index_needed else 'OK'}")
        print(f"share_clicks 表:         {'NEED CREATE' if share_clicks_needed else 'OK'}")
        print(f"share_button_clicks 表:  {'NEED CREATE' if share_btn_needed else 'OK'}")
        print(f"backfill referral_code:  {'NEEDED (詳見 apply log)' if any_user_needs_backfill else 'OK'}")
        print("=" * 72)
        result["status"] = "dry-run"
        return result

    try:
        bak = _backup()
        result["backup_path"] = str(bak)
    except Exception as e:
        logger.exception("[migration:009] backup failed")
        raise RuntimeError(f"backup failed: {e}") from e

    try:
        with engine.begin() as conn:
            # 1. ALTER TABLE users 加 3 欄
            for sql in users_missing_cols:
                logger.info("[migration:009] %s", sql)
                conn.execute(text(sql))
                result["users_cols_added"] += 1

            # 2. UNIQUE INDEX(在 backfill 之前 create — 確保 backfill 唯一性由 DB 把關)
            if users_index_needed:
                # 無資料時直接 create unique index 沒問題;有資料時要先確認沒重複
                # 因為剛 ADD COLUMN 預設都 NULL,UNIQUE 不阻擋多個 NULL(SQLite 行為)
                logger.info("[migration:009] %s", _SQL_USERS_INDEX)
                conn.execute(text(_SQL_USERS_INDEX))
                result["users_index"] = True

            # 3. share_clicks 表
            if share_clicks_needed:
                logger.info("[migration:009] CREATE TABLE share_clicks")
                conn.execute(text(_SQL_SHARE_CLICKS))
                for sql in _SQL_SHARE_CLICKS_INDEXES:
                    conn.execute(text(sql))
                result["share_clicks_created"] = True

            # 4. share_button_clicks 表
            if share_btn_needed:
                logger.info("[migration:009] CREATE TABLE share_button_clicks")
                conn.execute(text(_SQL_BUTTON_CLICKS))
                for sql in _SQL_BUTTON_CLICKS_INDEXES:
                    conn.execute(text(sql))
                result["share_button_clicks_created"] = True

            # 5. backfill referral_code(在同 transaction 內做)
            n = _backfill_ref_codes(conn)
            result["users_backfilled"] = n
            logger.info("[migration:009] backfilled %s users", n)

        # 6. 驗證
        with engine.connect() as conn:
            null_left = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE referral_code IS NULL")
            ).scalar() or 0
            if null_left > 0:
                raise RuntimeError(f"{null_left} users still NULL referral_code after backfill")

        result["status"] = "ok"
        logger.info("[migration:009] SUCCESS")
        return result

    except Exception as e:
        logger.exception("[migration:009] failed, restoring")
        try:
            _restore(bak)
        except Exception as rerr:
            logger.critical(
                "[migration:009] RESTORE FAILED -- manual: copy %s -> %s",
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
