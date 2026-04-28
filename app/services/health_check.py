"""每日健康度總檢查(紀律 #20 監控層)。

掃當天所有 sync_status,整理出哪些 source 沒跑 / 跑失敗 / 標 partial,
寫一筆 source='health_check' 的 sync_status row 留證據,並 log warning。

不做 TG 通知(留下個 commit)。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.database import session_scope
from app.models.sync_status import SyncStatus
from app.services.sync_status import record_sync_attempt

logger = logging.getLogger(__name__)

SYNC_SOURCE = "health_check"

# 我們關心的 sync 來源:這些每天都該跑 / 該成功
EXPECTED_SOURCES = (
    "kbar_sync",
    "dividend_sync",
    "etf_universe",
    "news_sync",
    "twse_dividend_announce",
    "holdings_cmoney",
)


def daily_health_check(today: date | None = None) -> dict:
    """每天 23:00 跑:檢查當天有沒漏跑或標 missing 的 sync。

    Returns:
        {ok: bool, today_runs: dict[source, info], notes: str}
    """
    today = today or date.today()
    today_start = datetime.combine(today, datetime.min.time())

    notes: list[str] = []
    today_runs: dict[str, dict] = {}

    with session_scope() as s:
        rows = s.scalars(
            select(SyncStatus).where(SyncStatus.last_attempt_at >= today_start)
        ).all()
        runs_by_source = {r.source: r for r in rows}

    for source in EXPECTED_SOURCES:
        r = runs_by_source.get(source)
        if r is None:
            today_runs[source] = {"status": "not_run"}
            notes.append(f"{source}: 今天沒跑")
            continue
        is_success = r.last_success_at is not None and (
            r.last_attempt_at is None or r.last_success_at >= today_start
        )
        partial = (r.missing_count or 0) > 0
        info = {
            "status": "partial" if partial else ("ok" if is_success else "failed"),
            "rows": r.rows_synced,
            "missing": r.missing_count,
            "last_error": r.last_error,
        }
        today_runs[source] = info
        if not is_success:
            notes.append(f"{source}: 跑失敗(last_error={(r.last_error or '')[:60]!r})")
        elif partial:
            notes.append(f"{source}: partial(missing {r.missing_count} 支)")

    ok = not notes
    summary_text = "; ".join(notes) if notes else "all sync healthy"

    # 寫一筆 health_check 到 sync_status
    record_sync_attempt(
        source=SYNC_SOURCE,
        success=ok,
        rows=len([s for s in EXPECTED_SOURCES if s in runs_by_source]),
        error=summary_text[:1900] if not ok else None,
        missing=[s for s in EXPECTED_SOURCES if s not in runs_by_source],
    )

    log_fn = logger.info if ok else logger.warning
    log_fn("[health_check] %s — %s", today.isoformat(), summary_text)

    return {
        "ok": ok,
        "today": today.isoformat(),
        "today_runs": today_runs,
        "notes": notes,
        "summary": summary_text,
    }


def retry_partial_sync_after_5min(missing_source: str) -> None:
    """簡化版 retry:5 分鐘後 one-shot retry 該 source。

    由 missing 不空的 sync 主動 schedule 用 — 但本 commit 暫不在 sync
    function 內 hook,只提供 helper 給 scheduler 之後接(避免 sync
    function 對 scheduler 反向依賴)。
    """
    # 留 stub:實作 schedule one-shot 在 scheduler 層比較乾淨
    # (避免 service 層 import APScheduler)
    raise NotImplementedError(
        "5-min retry should be scheduled at scheduler layer, see scheduler.py"
    )
