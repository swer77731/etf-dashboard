"""FinMind 配額(自家)— 限 33% (2000/hr)+ 自動暫停。

問題:user FinMind 6000/hr 但與朋友共用,user 只能用 33%。

策略:每筆呼叫 record 進 finmind_quota_log,計算過去 60 分鐘自家用量,
達上限就暫停到下個整點(取代 finmind.py 既有的 /user_info global ratio
判斷,因為那含朋友用量)。

公開:
- record_finmind_call(endpoint)
- count_recent_calls(window_min=60)
- check_finmind_quota() -> bool
- should_block() -> tuple[bool, int_minutes_until_next_hour]
- FinMindQuotaExhausted

紀律 #20 整合:
- 每次 record 順手 upsert sync_status('finmind_quota_check') 紀錄當前 60
  分鐘用量,監控頁可查歷史。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, text

from app.database import session_scope
from app.services.sync_status import record_sync_attempt

logger = logging.getLogger(__name__)


# user 限額:6000/hr × 33% ≈ 2000(留 67% 給朋友)
FINMIND_QUOTA_LIMIT = 2000
WINDOW_MINUTES = 60

SYNC_STATUS_SOURCE = "finmind_quota_check"


class FinMindQuotaExhausted(Exception):
    """自家 60 分鐘配額用盡。caller 應 catch 後 sync_status 標 partial。"""


# ──────────────────────────────────────────────────────────────────
# 寫入 / 查詢
# ──────────────────────────────────────────────────────────────────

def record_finmind_call(endpoint: str) -> None:
    """寫一筆 finmind_quota_log。caller 應在「成功打到 FinMind」後呼叫。

    用 Python localtime,跟 count_recent_calls 的 cutoff 一致(SQLite
    CURRENT_TIMESTAMP 是 UTC 會差 8 小時,不能依賴 schema default)。
    """
    now_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with session_scope() as s:
        s.execute(
            text("INSERT INTO finmind_quota_log (called_at, endpoint) VALUES (:t, :e)"),
            {"t": now_local, "e": endpoint[:64]},
        )


def count_recent_calls(window_min: int = WINDOW_MINUTES) -> int:
    """過去 N 分鐘自家呼叫筆數。"""
    cutoff = datetime.now() - timedelta(minutes=window_min)
    with session_scope() as s:
        return s.scalar(
            text("SELECT COUNT(*) FROM finmind_quota_log WHERE called_at >= :c"),
            {"c": cutoff.strftime("%Y-%m-%d %H:%M:%S")},
        ) or 0


def check_finmind_quota() -> bool:
    """True = 還有配額可用,False = 已達上限。

    判斷:過去 60 分鐘呼叫數 < FINMIND_QUOTA_LIMIT。
    """
    return count_recent_calls() < FINMIND_QUOTA_LIMIT


def _seconds_until_next_hour() -> int:
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return int((next_hour - now).total_seconds())


def should_block() -> tuple[bool, int]:
    """配額判斷 + 算到下個整點還剩幾分鐘。

    回 (block?, minutes_to_next_hour)。block=True 時 caller 應 sleep。
    """
    if check_finmind_quota():
        return False, 0
    secs = _seconds_until_next_hour()
    return True, max(1, (secs + 59) // 60)   # 向上取整


# ──────────────────────────────────────────────────────────────────
# sync_status 紀錄(紀律 #20:可監控)
# ──────────────────────────────────────────────────────────────────

def log_quota_status_to_sync_status() -> None:
    """寫 sync_status('finmind_quota_check')— 監控頁可查當前用量。

    rows_synced: 過去 60 分鐘實際呼叫數
    missing_count: max(0, actual - limit)(超額紀錄)
    success: actual <= limit
    """
    actual = count_recent_calls()
    over = max(0, actual - FINMIND_QUOTA_LIMIT)
    record_sync_attempt(
        source=SYNC_STATUS_SOURCE,
        success=(over == 0),
        rows=actual,
        error=(f"over quota: {actual}/{FINMIND_QUOTA_LIMIT}" if over > 0 else None),
        missing=([f"over_quota_{over}"] if over > 0 else None),
    )
