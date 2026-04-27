"""ETF 持股變動 — 近 N 日 buy / sell / new(從 CMoney holdings 10-day window 計算)。

每次 sync 寫一批新 row(同 etf 同 stock 同 updated_at 不重複)。
查最新變動 = 取該 etf_id 最新 updated_at 那批。
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
