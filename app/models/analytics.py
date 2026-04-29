"""客戶紀錄分析 — 取代 GA4。

3 張表:
- analytics_log:每筆 GET 訪問(扣掉排除清單 + 5 秒去重)
- search_log:autocomplete 非空 q 紀錄
- compare_log:/compare 的 codes 排序版紀錄

紀律 #16:
- IP 一律遮罩末段(124.156.222.xxx)— 不存原始 IP
- user_id 預留 NULL,等 Google OAuth 上線再填
- 90 天後自動 archived(daily 03:00 cron)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnalyticsLog(Base):
    __tablename__ = "analytics_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 預留 OAuth
    ip_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ua: Mapped[str | None] = mapped_column(String(512), nullable=True)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    query_string: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    referer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # UTC naive
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_analytics_ts", "ts"),
        Index("ix_analytics_session", "session_id"),
        Index("ix_analytics_path", "path"),
    )


class SearchLog(Base):
    __tablename__ = "search_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    q: Mapped[str] = mapped_column(String(128), nullable=False)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # 命中筆數
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_search_ts", "ts"),
        Index("ix_search_q", "q"),
    )


class CompareLog(Base):
    __tablename__ = "compare_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    codes_sorted: Mapped[str] = mapped_column(String(128), nullable=False)  # ',' joined,sorted
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_compare_ts", "ts"),
        Index("ix_compare_codes", "codes_sorted"),
    )
