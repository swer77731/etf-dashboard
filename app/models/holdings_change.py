"""ETF 持股變動 — 近 N 日 buy / sell / new(歷史 schema)。

2026-05-09 持股功能下架後保留 ORM,資料已 truncate。
schema 留作未來合法資料源(發行商月報 / 投信公會)重建持股功能時復用。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Date as SADate,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.etf import ETF


class HoldingsChange(Base):
    __tablename__ = "holdings_change"
    __table_args__ = (
        UniqueConstraint("etf_id", "stock_code", "updated_at", name="uq_holdings_change"),
        Index("ix_change_etf", "etf_id"),
        Index("ix_change_dir", "etf_id", "change_direction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    etf_id: Mapped[int] = mapped_column(
        ForeignKey("etf_list.id", ondelete="CASCADE"), nullable=False
    )

    stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(64), nullable=False)

    # buy / sell / new
    change_direction: Mapped[str] = mapped_column(String(8), nullable=False)

    # 持有股數差(增為正,減為負,新增 = s_new)
    shares_diff: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # 最新一日的權重(% — 給 UI 排序用)
    weight_latest: Mapped[float | None] = mapped_column(Float, nullable=True)

    latest_date: Mapped[date] = mapped_column(SADate, nullable=False)
    previous_date: Mapped[date] = mapped_column(SADate, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    etf: Mapped["ETF"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<HoldingsChange etf_id={self.etf_id} {self.stock_code} "
            f"{self.change_direction} {self.shares_diff:+d}>"
        )
