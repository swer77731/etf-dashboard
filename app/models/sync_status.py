"""排程同步狀態追蹤(Phase 1B-2 Step 2)。

每個資料源(twse_announce / dividend_finmind / kbar / news / etf_universe ...)
一筆紀錄,記錄最後一次嘗試 / 成功時間 / 失敗訊息 / 寫入筆數。

未來給 `/api/data-freshness` 端點 + 後台首頁紅點警示用。

落地紀律(plan 鎖定):
- source 是 primary key(每來源一筆,不存歷史 attempts)
- 失敗時 `last_success_at` **不變**(只動 last_attempt_at + last_error)
- 成功時 `last_error` 清回 None
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SyncStatus(Base):
    __tablename__ = "sync_status"

    # 資料源代號(eg. 'twse_announce', 'dividend_finmind', 'kbar', 'news', 'etf_universe',
    # 'holdings_yuanta', 'holdings_cathay' ...)
    source: Mapped[str] = mapped_column(String(64), primary_key=True)

    # 最後一次「嘗試」時間 — 不論成敗都更新
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 最後一次「成功」時間 — 失敗時不變
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 最後一次失敗訊息 — 成功時清空
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # 最後一次同步寫入筆數 — 給監控用
    rows_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # === 紀律 #20 資料完整性鐵律(2026-04-27 migration 003 加)===
    # retry escalation 計數(0 = 上次成功 / 1 = 已 retry 1 次 / >= 3 = 嚴重警告)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 缺漏項目數(expected - actual)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 缺漏清單(JSON list of identifiers,例:["00939", "00984D"])
    missing_items: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SyncStatus {self.source} "
            f"last_attempt={self.last_attempt_at} "
            f"last_success={self.last_success_at} "
            f"err={'Y' if self.last_error else 'N'} "
            f"retry={self.retry_count} miss={self.missing_count}>"
        )
