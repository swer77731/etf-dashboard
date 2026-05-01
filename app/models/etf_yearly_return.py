"""ETF 歷年含息報酬率 — 給定期定額試算器查 DB 用。

每支 ETF 每年一筆,is_partial=1 表示當年 YTD 未完整。
資料來源:FinMind TaiwanStockPriceAdj(還原價已含配息分割)。
"""
from __future__ import annotations

from sqlalchemy import Float, Index, Integer, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EtfYearlyReturn(Base):
    __tablename__ = "etf_yearly_returns"
    __table_args__ = (
        PrimaryKeyConstraint("etf_code", "year", name="pk_etf_yearly_returns"),
        Index("ix_etf_yearly_returns_code", "etf_code"),
    )

    etf_code: Mapped[str] = mapped_column(String(16), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    annual_return: Mapped[float] = mapped_column(Float, nullable=False)   # 0.234 = +23.4%
    data_source: Mapped[str] = mapped_column(String(32), nullable=False, default="finmind_adj")
    is_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # 1 = YTD,0 = 完整年
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)            # ISO8601

    def __repr__(self) -> str:  # pragma: no cover
        suffix = " (partial)" if self.is_partial else ""
        return f"<EtfYearlyReturn {self.etf_code} {self.year} {self.annual_return:+.4f}{suffix}>"
