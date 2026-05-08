"""One-shot DB migrations(SQLite,沒用 alembic)。

每個檔案 NNN_描述.py,內含 `run(dry_run=True)` 函式。
獨立執行:`python -m app.migrations.001_dividend_nullable_amounts [--apply]`

開機 auto-apply
================
2026-05-08 加上 — Zeabur 不會自動跑 migration,push 新程式時若新增了欄位,
container restart 但 DB schema 沒升級 → ORM SELECT 缺欄位炸 → 全站 500。

`apply_pending_migrations()` 在 lifespan 啟動時呼叫,每個 migration 內建
idempotent(已套用就 return status='skipped'),所以開機跑全部沒問題。
失敗 log + 繼續 — 與其因 migration 卡死讓全站不能用,不如讓既有功能可運作,
管理員看到 log 後手動排查。
"""
from __future__ import annotations

import importlib
import logging
import re

logger = logging.getLogger(__name__)

# 順序很重要 — 編號小的先跑(SQLite 沒交易間相依但邏輯有)
_MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+$")


def _list_migration_modules() -> list[str]:
    """掃描自身 package 找 NNN_xxx.py。"""
    import pkgutil
    import app.migrations as pkg
    names: list[str] = []
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.ispkg:
            continue
        if _MIGRATION_RE.match(m.name):
            names.append(m.name)
    names.sort()  # 按 NNN 編號排
    return names


def apply_pending_migrations() -> dict:
    """開機時跑全部 migration(每個 idempotent → 已跑就 skip)。

    回傳:
    {
        "ran": ["009_share_referral", ...],
        "skipped": ["001_...", ...],
        "failed": [{"name": "...", "error": "..."}, ...]
    }

    任一 migration 失敗 → log critical 但繼續處理下一個 / 啟動 web。
    """
    out = {"ran": [], "skipped": [], "failed": []}
    for name in _list_migration_modules():
        full = f"app.migrations.{name}"
        try:
            mod = importlib.import_module(full)
            run_fn = getattr(mod, "run", None)
            if not callable(run_fn):
                logger.warning("[migration:auto] %s — no run() function, skip", name)
                continue
            result = run_fn(dry_run=False)
            status = (result or {}).get("status", "unknown")
            if status == "skipped":
                out["skipped"].append(name)
            elif status == "ok":
                out["ran"].append(name)
                logger.info("[migration:auto] %s applied", name)
            else:
                # 'unknown' / 其他 — 視為已套用(部分舊 migration 不一定回 status='skipped')
                out["skipped"].append(name)
        except Exception as e:
            logger.exception("[migration:auto] %s FAILED — continuing", name)
            out["failed"].append({"name": name, "error": str(e)[:300]})
    if out["ran"]:
        logger.info("[migration:auto] applied: %s", ", ".join(out["ran"]))
    if out["failed"]:
        logger.critical(
            "[migration:auto] %d FAILED — admin should investigate: %s",
            len(out["failed"]),
            ", ".join(f["name"] for f in out["failed"]),
        )
    return out
