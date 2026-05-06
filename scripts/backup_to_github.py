"""每日 2 次自動備份 — push email-hashed etf.db 到 GitHub 私人 repo。

排程:由 app/scheduler.py 在 04:00 / 15:00 (Asia/Taipei) 觸發。也可手動執行:
    python scripts/backup_to_github.py
    python scripts/backup_to_github.py --force-monthly --force-yearly  # 測試用

流程
====
1. SQLite Connection.backup() 線上備份到 /tmp(不用 cp,寫入中也安全)
2. 開暫存 DB,把所有有 email 欄位的表 UPDATE 成 'sha256:<hex>'(idempotent)
3. gzip 壓縮 → daily/YYYY-MM-DD_HHMM.db.gz
4. 透過 GitHub API PUT 上傳;每月 1 號再上 monthly/YYYY-MM.db.gz;
   每年 1/1 再上 yearly/YYYY.db.gz(monthly/yearly 用 UPSERT,有 sha 就覆寫)
5. 列 daily/ 底下檔案,刪 90 天前的(monthly / yearly 不刪)
6. 每次上傳寫一筆 backup_log;失敗也寫(status='failed' + error_message)
7. 失敗一律 swallow,不要 raise(避免 cron 整個炸)

紀律
====
- 紀律 #18:GITHUB_BACKUP_TOKEN / GITHUB_BACKUP_REPO 只能 os.getenv 讀,不寫死
- 紀律 #18:任何 log / error message 都不能含 token / repo path 的細節(_redact 包過)
- 紀律 #20:備份是資料主權的一環,失敗一定要寫 backup_log 給後台監控頁看見
- D 槽鐵律:暫存檔走 tempfile.gettempdir()(Linux 容器 = /tmp、Windows = %TEMP%),不碰 D:
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import logging
import os
import re
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# 確保 import 路徑包含專案根
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

logger = logging.getLogger("backup_to_github")

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

TAIPEI_TZ = timezone(timedelta(hours=8))
GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
DAILY_RETAIN_DAYS = 90
DAILY_FILENAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_\d{4}\.db\.gz$")
SHA256_PREFIX = "sha256:"
HTTP_TIMEOUT = 60.0  # 大 DB 上傳要時間

# ─────────────────────────────────────────────────────────────
# 敏感字串 redact(紀律 #18)
# ─────────────────────────────────────────────────────────────


def _redact(s: str) -> str:
    """打碼 token / repo path,避免外洩到 log / error message。"""
    if not s:
        return s
    token = os.getenv("GITHUB_BACKUP_TOKEN")
    repo = os.getenv("GITHUB_BACKUP_REPO")
    out = s
    if token:
        out = out.replace(token, "***")
    if repo:
        # repo path 也視為敏感(免費帳號掛 private repo,不需要對外曝露)
        out = out.replace(repo, "<repo>")
    return out


# ─────────────────────────────────────────────────────────────
# Step 1 — SQLite 線上備份
# ─────────────────────────────────────────────────────────────


def _online_backup_sqlite(src_path: Path, dst_path: Path) -> None:
    """sqlite3 內建 BACKUP API — 比 shutil.copy 安全(寫入中也保證一致性)。"""
    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(dst_path))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()


# ─────────────────────────────────────────────────────────────
# Step 2 — Hash email 欄位
# ─────────────────────────────────────────────────────────────


def _tables_with_email(conn: sqlite3.Connection) -> list[str]:
    """掃所有 user-defined table,挑有 email 欄位的。"""
    out: list[str] = []
    cursor = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    for (table_name,) in cursor.fetchall():
        info = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        cols = {row[1].lower() for row in info}
        if "email" in cols:
            out.append(table_name)
    return out


def _hash_email_values(db_path: Path) -> dict:
    """打開 db_path、把所有 email 欄位 hash 化。回傳統計。"""
    stats = {"tables": [], "rows_hashed": 0, "rows_skipped": 0}
    conn = sqlite3.connect(str(db_path))
    try:
        tables = _tables_with_email(conn)
        for table in tables:
            t_stat = {"table": table, "hashed": 0, "skipped": 0}
            rows = conn.execute(
                f'SELECT rowid, email FROM "{table}"'
            ).fetchall()
            for rowid, email in rows:
                if email is None:
                    t_stat["skipped"] += 1
                    continue
                if isinstance(email, str) and email.startswith(SHA256_PREFIX):
                    t_stat["skipped"] += 1
                    continue
                h = hashlib.sha256(str(email).encode("utf-8")).hexdigest()
                conn.execute(
                    f'UPDATE "{table}" SET email = ? WHERE rowid = ?',
                    (f"{SHA256_PREFIX}{h}", rowid),
                )
                t_stat["hashed"] += 1
            stats["tables"].append(t_stat)
            stats["rows_hashed"] += t_stat["hashed"]
            stats["rows_skipped"] += t_stat["skipped"]
        conn.commit()
    finally:
        conn.close()
    return stats


# ─────────────────────────────────────────────────────────────
# Step 3 — gzip 壓縮
# ─────────────────────────────────────────────────────────────


def _gzip_file(src: Path, dst: Path) -> None:
    """raw db → gzip。compresslevel=6 是 size/cpu 平衡點。"""
    with open(src, "rb") as fin, gzip.open(dst, "wb", compresslevel=6) as fout:
        # 64KB chunk 平衡 memory + IO
        while True:
            chunk = fin.read(65536)
            if not chunk:
                break
            fout.write(chunk)


# ─────────────────────────────────────────────────────────────
# Step 4 — GitHub API
# ─────────────────────────────────────────────────────────────


class GitHubBackupClient:
    """簡薄包裝 — Contents API 的 PUT / GET / DELETE。

    repo 用 env var GITHUB_BACKUP_REPO('owner/repo')。
    token 用 env var GITHUB_BACKUP_TOKEN(fine-grained 或 classic 都行,需 Contents:write)。
    """

    def __init__(self, token: str, repo: str):
        self._repo = repo  # 內部用,不對外
        self._client = httpx.Client(
            base_url=f"{GITHUB_API}/repos/{repo}",
            timeout=HTTP_TIMEOUT,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "etf-watch-backup/1.0",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get_file_sha(self, path: str) -> str | None:
        """檔案存在 → 回 sha;不存在 → None。其他狀態 → raise。"""
        r = self._client.get(f"/contents/{path}")
        if r.status_code == 404:
            return None
        if r.status_code == 200:
            data = r.json()
            # contents API 在路徑指到單檔時回 dict、目錄時回 list
            if isinstance(data, dict) and "sha" in data:
                return data["sha"]
            return None
        raise RuntimeError(
            f"GitHub get_file_sha unexpected {r.status_code}: {_redact(r.text[:200])}"
        )

    def put_file(self, path: str, content: bytes, message: str) -> dict:
        """PUT /contents/{path}。檔案存在則 UPSERT(帶 sha 蓋寫)。"""
        body = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
        }
        sha = self.get_file_sha(path)
        if sha:
            body["sha"] = sha
        r = self._client.put(f"/contents/{path}", json=body)
        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"GitHub put_file {r.status_code}: {_redact(r.text[:300])}"
            )
        return r.json()

    def list_dir(self, path: str) -> list[dict]:
        """目錄不存在 → []。其他 ok 回 list of {name, sha, size, ...}。"""
        r = self._client.get(f"/contents/{path}")
        if r.status_code == 404:
            return []
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else []
        raise RuntimeError(
            f"GitHub list_dir {r.status_code}: {_redact(r.text[:200])}"
        )

    def delete_file(self, path: str, sha: str, message: str) -> None:
        body = {"message": message, "sha": sha}
        r = self._client.request("DELETE", f"/contents/{path}", json=body)
        if r.status_code not in (200, 204):
            raise RuntimeError(
                f"GitHub delete_file {r.status_code}: {_redact(r.text[:200])}"
            )


# ─────────────────────────────────────────────────────────────
# 90 天保留策略
# ─────────────────────────────────────────────────────────────


def _apply_retention(client: GitHubBackupClient, today: date) -> dict:
    """刪 daily/ 下超過 90 天的檔案。失敗的記下繼續。"""
    out = {"checked": 0, "deleted": 0, "skipped": 0, "errors": 0}
    cutoff = today - timedelta(days=DAILY_RETAIN_DAYS)
    items = client.list_dir("daily")
    for it in items:
        out["checked"] += 1
        name = it.get("name", "")
        m = DAILY_FILENAME_RE.match(name)
        if not m:
            out["skipped"] += 1
            continue
        try:
            file_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            out["skipped"] += 1
            continue
        if file_date < cutoff:
            try:
                client.delete_file(
                    f"daily/{name}",
                    sha=it["sha"],
                    message=f"retention: prune {name} (older than {DAILY_RETAIN_DAYS} days)",
                )
                out["deleted"] += 1
                logger.info("[retention] deleted %s", name)
            except Exception as e:
                out["errors"] += 1
                logger.error("[retention] delete failed for %s: %s", name, _redact(str(e)))
    return out


# ─────────────────────────────────────────────────────────────
# backup_log 寫入
# ─────────────────────────────────────────────────────────────


def _write_log(
    backup_type: str,
    file_path: str | None,
    file_size_bytes: int | None,
    status: str,
    error_message: str | None,
    duration_seconds: float,
) -> None:
    """寫一筆 backup_log。寫失敗只 log,不 raise。"""
    try:
        from app.database import session_scope
        from app.models.backup_log import BackupLog

        with session_scope() as s:
            row = BackupLog(
                backup_type=backup_type,
                file_path=file_path,
                file_size_bytes=file_size_bytes,
                status=status,
                error_message=(error_message[:2000] if error_message else None),
                duration_seconds=round(duration_seconds, 3),
            )
            s.add(row)
    except Exception:
        logger.exception("[backup_log] write failed (non-fatal)")


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────


def _now_taipei() -> datetime:
    return datetime.now(tz=TAIPEI_TZ)


def _build_paths(now: datetime, force_monthly: bool, force_yearly: bool) -> list[tuple[str, str]]:
    """回傳 [(backup_type, github_path), ...]。daily 一定有,monthly/yearly 視日期。"""
    daily_name = now.strftime("%Y-%m-%d_%H%M") + ".db.gz"
    out = [("daily", f"daily/{daily_name}")]
    if force_monthly or now.day == 1:
        out.append(("monthly", f"monthly/{now.strftime('%Y-%m')}.db.gz"))
    if force_yearly or (now.month == 1 and now.day == 1):
        out.append(("yearly", f"yearly/{now.strftime('%Y')}.db.gz"))
    return out


def _do_one_upload(
    client: GitHubBackupClient,
    backup_type: str,
    gh_path: str,
    payload: bytes,
    commit_message: str,
) -> dict:
    """上傳單一檔案 + 寫 backup_log(成功 / 失敗都寫)。"""
    started = time.time()
    out = {"backup_type": backup_type, "path": gh_path, "size": len(payload), "status": "failed"}
    try:
        client.put_file(gh_path, payload, commit_message)
        elapsed = time.time() - started
        out["status"] = "success"
        out["duration"] = elapsed
        _write_log(backup_type, gh_path, len(payload), "success", None, elapsed)
        logger.info("[upload] OK %s (%d bytes, %.1fs)", gh_path, len(payload), elapsed)
    except Exception as e:
        elapsed = time.time() - started
        msg = _redact(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        out["error"] = msg[:500]
        out["duration"] = elapsed
        _write_log(backup_type, gh_path, len(payload), "failed", msg, elapsed)
        logger.error("[upload] FAILED %s: %s", gh_path, _redact(str(e)))
    return out


def run_backup(force_monthly: bool = False, force_yearly: bool = False) -> dict:
    """主入口 — cron / CLI 都呼這個。失敗一律 swallow。"""
    overall_started = time.time()
    summary = {"ok": False, "uploads": [], "retention": None, "hash": None, "error": None}

    token = os.getenv("GITHUB_BACKUP_TOKEN", "").strip()
    repo = os.getenv("GITHUB_BACKUP_REPO", "").strip()
    if not token or not repo:
        msg = "GITHUB_BACKUP_TOKEN / GITHUB_BACKUP_REPO env not set"
        logger.error("[backup] %s", msg)
        _write_log(
            "daily", None, None, "failed", msg,
            time.time() - overall_started,
        )
        summary["error"] = msg
        return summary

    # 確保 db 檔可被 import 用 — 用 app.config 的解析
    try:
        from app.config import DATA_DIR  # noqa: F401  (確保 DATA_DIR 存在)
        from app.database import init_db
        init_db()  # idempotent — 確保 backup_log 表存在
    except Exception as e:
        logger.exception("[backup] init_db failed")
        summary["error"] = _redact(str(e))
        # 即便 init_db 失敗也要繼續 — DB 檔仍可備份(只是 backup_log 寫不了)

    src_db = ROOT / "data" / "etf.db"
    if not src_db.exists():
        msg = f"DB file not found: {src_db}"
        logger.error("[backup] %s", msg)
        _write_log("daily", None, None, "failed", msg, time.time() - overall_started)
        summary["error"] = msg
        return summary

    # 暫存目錄(/tmp on Linux,Windows %TEMP%) — 紀律:不碰 D:
    tmp_dir = Path(tempfile.gettempdir())
    tmp_db = tmp_dir / "etf_backup.db"
    tmp_gz = tmp_dir / f"etf_backup_{int(time.time())}.db.gz"

    try:
        # Step 1 — 線上備份
        logger.info("[backup] step 1: online backup -> %s", tmp_db)
        if tmp_db.exists():
            tmp_db.unlink()
        _online_backup_sqlite(src_db, tmp_db)

        # Step 2 — Hash email
        logger.info("[backup] step 2: hash email columns")
        hash_stat = _hash_email_values(tmp_db)
        summary["hash"] = hash_stat
        logger.info(
            "[backup] hashed %d row(s) across %d table(s)",
            hash_stat["rows_hashed"], len(hash_stat["tables"]),
        )

        # Step 3 — gzip
        logger.info("[backup] step 3: gzip")
        _gzip_file(tmp_db, tmp_gz)
        payload = tmp_gz.read_bytes()
        logger.info("[backup] gzip size = %d bytes", len(payload))

        # Step 4 — 推到 GitHub
        now = _now_taipei()
        paths = _build_paths(now, force_monthly, force_yearly)
        commit_msg = f"backup {now.strftime('%Y-%m-%d %H:%M')} TPE"
        with GitHubBackupClient(token, repo) as client:
            for backup_type, gh_path in paths:
                up = _do_one_upload(client, backup_type, gh_path, payload, commit_msg)
                summary["uploads"].append(up)

            # Step 5 — 90 天保留(daily/ only)
            try:
                summary["retention"] = _apply_retention(client, now.date())
                logger.info("[backup] retention: %s", summary["retention"])
            except Exception as e:
                logger.error("[backup] retention failed: %s", _redact(str(e)))
                summary["retention"] = {"error": _redact(str(e))}

        any_ok = any(u["status"] == "success" for u in summary["uploads"])
        summary["ok"] = any_ok
    except Exception as e:
        msg = _redact(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        logger.error("[backup] fatal: %s", msg[:500])
        summary["error"] = msg[:500]
        # 至少寫一筆 daily failed log,好讓後台監控頁不至於完全空白
        if not summary["uploads"]:
            _write_log(
                "daily", None, None, "failed", msg,
                time.time() - overall_started,
            )
    finally:
        # 清暫存(無論成敗)
        for f in (tmp_db, tmp_gz):
            try:
                if f.exists():
                    f.unlink()
            except Exception:
                logger.warning("[backup] cleanup failed for %s", f)

    return summary


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    p = argparse.ArgumentParser(description="Backup etf.db to GitHub private repo")
    p.add_argument("--force-monthly", action="store_true",
                   help="force generate monthly/ snapshot (otherwise only on day 1)")
    p.add_argument("--force-yearly", action="store_true",
                   help="force generate yearly/ snapshot (otherwise only on Jan 1)")
    args = p.parse_args()

    out = run_backup(force_monthly=args.force_monthly, force_yearly=args.force_yearly)
    print("\n=== Backup summary ===")
    print(f"  ok:        {out['ok']}")
    if out.get("hash"):
        h = out["hash"]
        print(f"  hashed:    {h['rows_hashed']} rows / {len(h['tables'])} tables")
    for u in out.get("uploads", []):
        size_kb = (u.get("size") or 0) / 1024
        print(f"  upload {u['backup_type']:7s} -> {u['path']}  "
              f"{u['status']}  ({size_kb:.1f} KB, {u.get('duration', 0):.1f}s)")
    if out.get("retention"):
        print(f"  retention: {out['retention']}")
    if out.get("error"):
        print(f"  error:     {out['error'][:200]}")
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
