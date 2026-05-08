"""Maintenance / readiness state(2026-05-08)。

兩個 flag:
- `_app_ready`: lifespan 啟動完成才 True;部署期間 False → middleware 回 503
- `_manual_maintenance`: admin 手動切換,True 強制顯示維護頁

middleware 與 admin endpoints 都從這個模組讀寫。Python module-level vars
讀寫是 atomic(GIL),單 worker 場景無 race。多 worker 場景無共享 state
但可接受 — Zeabur 預設單 worker。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 啟動 readiness — lifespan 跑完 critical init 才設 True
_app_ready: bool = False

# 手動維護模式 — admin 切換
_manual_maintenance: bool = False


def set_app_ready(value: bool) -> None:
    global _app_ready
    _app_ready = value
    logger.info("[maintenance] app_ready = %s", value)


def is_app_ready() -> bool:
    return _app_ready


def set_manual_maintenance(value: bool) -> None:
    global _manual_maintenance
    _manual_maintenance = value
    logger.info("[maintenance] manual = %s", value)


def is_manual_maintenance() -> bool:
    return _manual_maintenance


def is_under_maintenance() -> bool:
    """app 未就緒 OR admin 手動開啟 → 維護模式。"""
    return (not _app_ready) or _manual_maintenance
