"""ETF 配息資料 — Total Return (B 公式) 計算用。

來源:FinMind `TaiwanStockDividend`
B 公式:`return = (期末 raw close + 期間累積現金股利) / 期初 raw close - 1`
"""
from __future__ import annotations

from datetime import date as Date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date as SADate,
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


class Dividend(Base):
    __tablename__ = "dividend"
    __table_args__ = (
        UniqueConstraint("etf_id", "ex_date", name="uq_dividend_etf_exdate"),
        Index("ix_dividend_etf_exdate", "etf_id", "ex_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    etf_id: Mapped[int] = mapped_column(ForeignKey("etf_list.id", ondelete="CASCADE"), nullable=False)
    ex_date: Mapped[Date] = mapped_column(SADate, nullable=False)

    # NULL = 未公告 / 未知(TWSE 預告表「待公告」row)
    # 0.0  = 真的配 0 元(罕見但理論可能,如下市清算)
    # UI 對應:NULL → 「待公告」灰字 / 0.0 → 「0 元」
    # Migration 001 (2026-04-27) 從 NOT NULL DEFAULT 0.0 改成 nullable no default
    cash_dividend: Mapped[float | None] = mapped_column(Float, nullable=True)
    stock_dividend: Mapped[float | None] = mapped_column(Float, nullable=True)

    payment_date: Mapped[Date | None] = mapped_column(SADate, nullable=True)
    announce_date: Mapped[Date | None] = mapped_column(SADate, nullable=True)

    # FinMind 報的「公司會計年度」(民國年),保留追蹤
    fiscal_year: Mapped[str | None] = mapped_column(String(8), nullable=True)

    etf: Mapped["ETF"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Dividend etf_id={self.etf_id} ex={self.ex_date} cash={self.cash_dividend}>"
