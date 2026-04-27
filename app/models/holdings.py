"""ETF 個股持股資料 — Phase Holdings(/holdings 頁用)。

每支 ETF 每次 sync 寫一批 row(rank 1~10 個股),用 updated_at 區分批次。
查最新持股 = 取該 etf_id 最新 updated_at 那批。

來源:各投信公開揭露(元大 / 國泰 / 富邦 / ...),爬蟲抓進本地 DB。
排程:每週一 14:30(持股變動慢,週更新夠用)。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.etf import ETF


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (
        # 同 ETF 同個股同批次只能一筆(避免 sync 重複寫)
        UniqueConstraint("etf_id", "stock_code", "updated_at", name="uq_holdings_etf_stock_date"),
        Index("ix_holdings_etf", "etf_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # 跟 dividend / kbar 一致用 etf_id FK(plan Q1)
    etf_id: Mapped[int] = mapped_column(
        ForeignKey("etf_list.id", ondelete="CASCADE"), nullable=False
    )

    # 個股(台股代號 + 名稱)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(64), nullable=False)

    # 持股比重(% 例:7.85 表示 7.85%)
    weight: Mapped[float] = mapped_column(Float, nullable=False)

    # 產業字串(例:「半導體」)— 不做標準化(plan 規定),用發行商給的字串
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 該批 ETF 持股的 rank(1 = 最大持股)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    # 該批次 sync 時間(批次 key — 同 etf 不同批次 updated_at 不同)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # 來源(例:「yuanta」、「cathay」),給管理 + 監控用
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    etf: Mapped["ETF"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Holding etf_id={self.etf_id} {self.stock_code} "
            f"{self.stock_name} {self.weight:.2f}% rank={self.rank}>"
        )
