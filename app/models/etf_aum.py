"""ETF 規模(AUM)月歷史 — SITCA etf_statement2.aspx?txtYM=YYYYMM&txtR1=0 月更。

month_date 約定:釘月份第 1 天(例:2026-03 → 2026-03-01)。一致、可 query、
UNIQUE (etf_code, month_date) 不撞。

aum_thousand_ntd:以千元(NTD)儲存的整數,避免 REAL 浮點誤差。

  raw NTD (元) → round(raw / 1000) → thousand_ntd INT → 存 DB
  thousand_ntd / 1e5 → 億元 → 前台顯示(1 位小數)

例:0050 raw 1_316_734_572_619 元 → round(/1000) = 1_316_734_573 千元
   → /1e5 = 13167.3 億

注意:Phase 3 fetcher 用 Python 內建 `round()`(銀行家捨入,half-to-even)
而非 `//`(無條件捨去)— 金融資料約定。helper 在 aum_sync.py 含 unit test。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EtfAum(Base):
    __tablename__ = "etf_aum"
    __table_args__ = (
        UniqueConstraint("etf_code", "month_date", name="uq_aum_etf_month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    month_date: Mapped[date] = mapped_column(Date, nullable=False)
    aum_thousand_ntd: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        billion = self.aum_thousand_ntd / 1e5
        return (
            f"<EtfAum {self.etf_code} {self.month_date} "
            f"{billion:.1f}億>"
        )
