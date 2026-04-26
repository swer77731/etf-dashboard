"""Daily K-bar (OHLCV) model.

兩組價格欄位:
- 原始(open / high / low / close):用來顯示「目前股價」,跟券商 APP 一致
- 還原(adj_*):用來算所有報酬率、畫走勢圖(自動處理分割 / 配息 / 增資)
"""
from __future__ import annotations

from datetime import date as Date
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Date as SADate,
    Float,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.etf import ETF


class DailyKBar(Base):
    __tablename__ = "daily_kbar"
    __table_args__ = (
        UniqueConstraint("etf_id", "date", name="uq_daily_kbar_etf_date"),
        Index("ix_daily_kbar_etf_date", "etf_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    etf_id: Mapped[int] = mapped_column(ForeignKey("etf_list.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[Date] = mapped_column(SADate, nullable=False)

    # 原始價(顯示「目前股價」用)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # 還原價(算報酬率 / 畫走勢圖用) — 可能為 NULL(指數類沒有還原價)
    adj_open: Mapped[float | None] = mapped_column(Float, nullable=True)
    adj_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    adj_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)

    etf: Mapped["ETF"] = relationship(back_populates="kbars")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KBar etf_id={self.etf_id} {self.date} close={self.close}>"
