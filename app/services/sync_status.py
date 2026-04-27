"""sync_status 表的 helper(Phase 1B-2 Step 2 + 紀律 #20 升級)。

提供一行 call 的 `record_sync_attempt()`,讓任何 _sync.py / persist() 結束後
記錄成敗 + 筆數 + 缺漏項目。

落地紀律:
- 失敗時 `last_success_at` **絕對不變**(舊資料仍可用、UI 知道幾天沒新)
- 成功時 `last_error` 清回 None(避免幽靈警告)
- `last_attempt_at` 不論成敗都更新
- source 為 PK,每來源一筆;不存 attempt history(留給 log 系統)

紀律 #20(2026-04-27 by user):
- `missing` 參數 → 持久化進 missing_count + missing_items(JSON)
- 成功且 missing 為空 → `retry_count = 0`(reset)
- 失敗 → `retry_count` 不在這層處理(由排程層 retry escalation)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.database import session_scope
from app.models.sync_status import SyncStatus

logger = logging.getLogger(__name__)

# 訊息上限(避免 stack trace 太長爆 DB)
_ERROR_MAX_LEN = 1900
# missing_items JSON 字串上限(避免 1000+ 缺漏 identifier 爆 DB)
_MISSING_JSON_MAX_LEN = 8000


def record_sync_attempt(
    source: str,
    success: bool,
    rows: int = 0,
    error: Optional[str] = None,
    missing: Optional[list[str]] = None,
) -> SyncStatus:
    """記錄一次同步嘗試(紀律 #20 升級版)。

    Args:
        source: 資料源代號(eg. 'twse_announce', 'holdings_yuanta')
        success: 是否成功
        rows: 此次寫入筆數(失敗通常 0)
        error: 失敗訊息(success=True 時應傳 None)
        missing: 缺漏的 identifier 清單(例:['0050', '00981A'])
                 紀律 #20 — 預期應抓但未抓到的項目,寫進 missing_count + missing_items

    Returns:
        更新後的 SyncStatus row(已 detach,可安全在 session 外讀取)。
    """
    now = datetime.now()
    err_msg: Optional[str] = None
    if not success:
        err_msg = (error or "unknown error")[:_ERROR_MAX_LEN]

    miss_list = list(missing or [])
    miss_count = len(miss_list)
    miss_json = json.dumps(miss_list, ensure_ascii=False)
    if len(miss_json) > _MISSING_JSON_MAX_LEN:
        # 太長就截斷,只留前 N 個 + 標記
        truncated = miss_list[: max(1, _MISSING_JSON_MAX_LEN // 20)]
        miss_json = json.dumps(
            truncated + [f"...(+{miss_count - len(truncated)} more)"],
            ensure_ascii=False,
        )

    with session_scope() as s:
        row = s.scalar(select(SyncStatus).where(SyncStatus.source == source))
        if row is None:
            row = SyncStatus(
                source=source,
                last_attempt_at=now,
                last_success_at=now if success else None,
                last_error=None if success else err_msg,
                rows_synced=rows,
                retry_count=0,
                missing_count=miss_count,
                missing_items=miss_json,
            )
            s.add(row)
        else:
            # 永遠更新 attempt + rows + missing
            row.last_attempt_at = now
            row.rows_synced = rows
            row.missing_count = miss_count
            row.missing_items = miss_json
            if success:
                row.last_success_at = now
                row.last_error = None
                # 成功 + 無缺漏 → retry 計數歸零(紀律 #20)
                if miss_count == 0:
                    row.retry_count = 0
            else:
                # 紀律 #16 #20 延續:失敗不動 last_success_at
                row.last_error = err_msg
                # retry_count 不在這層加(由排程 retry escalation 自己加)

        s.flush()
        s.expunge(row)
    return row


def get_sync_status(source: str) -> SyncStatus | None:
    """讀單一 source 的最新狀態。"""
    with session_scope() as s:
        row = s.scalar(select(SyncStatus).where(SyncStatus.source == source))
        if row is None:
            return None
        s.expunge(row)
        return row


def list_all_sync_status() -> list[SyncStatus]:
    """全部 source 列出 — 給 /api/data-freshness 用。"""
    with session_scope() as s:
        rows = s.scalars(select(SyncStatus).order_by(SyncStatus.source.asc())).all()
        for r in rows:
            s.expunge(r)
        return list(rows)


if __name__ == "__main__":
    """獨立 smoke test — 直接 python -m app.services.sync_status

    驗收:
      1. 成功 attempt → 三欄都有值(attempt / success / rows),error=None
      2. 失敗 attempt → last_error 有值,**last_success_at 不變**(留住前一次成功)
      3. 重新成功 → last_error 清空,last_success_at 更新
    """
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)-7s | %(message)s")

    # 確保 table 存在
    from app.database import init_db
    init_db()

    # 清掉測試 source(每次跑乾淨)
    with session_scope() as s:
        existing = s.scalar(select(SyncStatus).where(SyncStatus.source == "test_source"))
        if existing:
            s.delete(existing)

    print("\n--- Test 1: 第一次成功 ---")
    r = record_sync_attempt("test_source", success=True, rows=5)
    print(f"  source={r.source} attempt={r.last_attempt_at} success={r.last_success_at}")
    print(f"  rows={r.rows_synced} error={r.last_error!r}")
    assert r.last_attempt_at is not None
    assert r.last_success_at is not None
    assert r.last_error is None
    assert r.rows_synced == 5
    print("  [PASS]")

    first_success_at = r.last_success_at

    print("\n--- Test 2: 失敗 — last_success_at 不能變 ---")
    r = record_sync_attempt("test_source", success=False, rows=0, error="fake API timeout")
    print(f"  attempt={r.last_attempt_at} success={r.last_success_at}")
    print(f"  rows={r.rows_synced} error={r.last_error!r}")
    assert r.last_error == "fake API timeout"
    assert r.last_success_at == first_success_at, (
        f"last_success_at changed! before={first_success_at} after={r.last_success_at}"
    )
    assert r.rows_synced == 0
    print("  [PASS] last_success_at preserved")

    print("\n--- Test 3: 重新成功 — last_error 清空 ---")
    r = record_sync_attempt("test_source", success=True, rows=12)
    print(f"  attempt={r.last_attempt_at} success={r.last_success_at}")
    print(f"  rows={r.rows_synced} error={r.last_error!r}")
    assert r.last_error is None
    assert r.last_success_at != first_success_at, "last_success_at should advance"
    assert r.rows_synced == 12
    print("  [PASS]")

    print("\n--- Test 4: missing 參數(紀律 #20)---")
    r = record_sync_attempt(
        "test_source", success=False, rows=8, error="2 etfs failed",
        missing=["00939", "00984D"],
    )
    print(f"  missing_count={r.missing_count} missing_items={r.missing_items}")
    assert r.missing_count == 2
    assert "00939" in r.missing_items and "00984D" in r.missing_items
    print("  [PASS] missing 持久化")

    # 紀律 #20:成功 + 無缺漏 → retry_count = 0
    r = record_sync_attempt("test_source", success=True, rows=10, missing=[])
    print(f"  after success retry_count={r.retry_count} missing_count={r.missing_count}")
    assert r.retry_count == 0
    assert r.missing_count == 0
    assert r.missing_items == "[]"
    print("  [PASS] retry_count reset")

    print("\n--- Test 5: get_sync_status / list_all_sync_status ---")
    one = get_sync_status("test_source")
    assert one is not None
    print(f"  get_sync_status('test_source') = {one}")

    nonexistent = get_sync_status("nonexistent_source")
    assert nonexistent is None
    print(f"  get_sync_status('nonexistent_source') = None [PASS]")

    all_rows = list_all_sync_status()
    print(f"  list_all_sync_status() → {len(all_rows)} rows: {[r.source for r in all_rows]}")

    print("\n--- Cleanup ---")
    with session_scope() as s:
        existing = s.scalar(select(SyncStatus).where(SyncStatus.source == "test_source"))
        if existing:
            s.delete(existing)
    print("  test_source 已清除")

    print("\n[ALL TESTS PASSED]")
