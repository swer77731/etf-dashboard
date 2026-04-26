"""Daily K-bar (OHLCV) model."""
from __future__ import annotations

from datetime import date as Date
from typing import TYPE_CHECKING

from sqlalchemy import Date as SADate, Float, ForeignKey, Index, Integer, UniqueConstraint
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
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    etf: Mapped["ETF"] = relationship(back_populates="kbars")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KBar etf_id={self.etf_id} {self.date} close={self.close}>"
