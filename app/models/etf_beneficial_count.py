"""ETF 受益人數歷史 — FinMind TaiwanStockHoldingSharesPer 週更。

week_date 約定:存 FinMind 回傳的原始 date(該週最後一個交易日,通常週五,
連假則前一個交易日)。不 normalize 到週一/週日。FinMind 自家保證同 ETF 同
週只回 1 個 date,UNIQUE (etf_code, week_date) 不會撞。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EtfBeneficialCount(Base):
    __tablename__ = "etf_beneficial_count"
    __table_args__ = (
        UniqueConstraint("etf_code", "week_date", name="uq_beneficial_etf_week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    week_date: Mapped[date] = mapped_column(Date, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EtfBeneficialCount {self.etf_code} {self.week_date} "
            f"count={self.count}>"
        )
