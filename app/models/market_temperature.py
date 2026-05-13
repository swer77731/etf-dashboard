"""市場溫度計相關 ORM models。

5 個資料源(對應 5 個 sync 服務):
1. MarginMaintenance — 大盤融資維持率(每日,XQ 含 ETF 口徑)
2. MarketBreadth     — 漲跌家數(每日,TWSE 純股票)
3. MarginShortTotal  — 融資 + 融券大盤合計餘額(每日,張)
4. SecuritiesLendingDaily — 借券當日交易(volume / 筆數 / 平均費率)
5. InstitutionalDaily — 三大法人現貨/期貨/選擇權合一寬表(每日 × 3 法人)

紀律 #20:每個 sync 都要走 record_sync_attempt + missing_items 完整性檢查。
紀律 #22:audit 端定期掃資料是否最新天有 row,缺漏進「人工待辦」。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarginMaintenance(Base):
    """大盤融資維持率(每日 1 row)。

    公式 = sum(融資餘額_張 × 收盤價) / 大盤融資金額(分母) × 100
    XQ 口徑(含 ETF):跟我們 v5 算的一致。
    """
    __tablename__ = "margin_maintenance"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    ratio_pct: Mapped[float] = mapped_column(Float, nullable=False)
    numerator_yi: Mapped[float] = mapped_column(Float, nullable=False)
    denominator_yi: Mapped[float] = mapped_column(Float, nullable=False)
    stock_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class MarketBreadth(Base):
    """漲跌家數(每日 1 row,TWSE MI_INDEX 純股票口徑)。"""
    __tablename__ = "market_breadth"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    up_count: Mapped[int] = mapped_column(Integer, nullable=False)
    down_count: Mapped[int] = mapped_column(Integer, nullable=False)
    flat_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class MarginShortTotal(Base):
    """融資 + 融券大盤合計餘額(每日 1 row,張數)。

    來源 FinMind TaiwanStockMarginPurchaseShortSale sum 全市場 twse-only。
    """
    __tablename__ = "margin_short_total"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    margin_balance: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 融資餘額(張)
    short_balance: Mapped[int] = mapped_column(BigInteger, nullable=False)   # 融券餘額(張)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class SecuritiesLendingDaily(Base):
    """借券當日交易(每日 1 row)。

    volume 加總(張) / 成交筆數 / 平均費率(volume-weighted)。
    來源 FinMind TaiwanStockSecuritiesLending。
    """
    __tablename__ = "securities_lending_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deal_count: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_fee_rate: Mapped[float] = mapped_column(Float, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class InstitutionalDaily(Base):
    """三大法人 — 現貨/期貨/選擇權合一寬表(每日 × 3 法人 = 3 row/day)。

    institution: 'foreign' / 'trust' / 'dealer'
    現貨:億元 / 期貨:口 / 選擇權:億元(僅 foreign 有 4 項細部 + put/call net)
    """
    __tablename__ = "institutional_daily"
    __table_args__ = (
        UniqueConstraint("date", "institution", name="uq_inst_date_who"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    institution: Mapped[str] = mapped_column(String(16), nullable=False)

    # 現貨買賣超(億元)
    spot_net_yi: Mapped[float | None] = mapped_column(Float)
    # 臺指期 TX 未平倉(口)
    fut_long_vol: Mapped[int | None] = mapped_column(Integer)
    fut_short_vol: Mapped[int | None] = mapped_column(Integer)
    # 選擇權 TXO(億元,僅 foreign 完整 4 項;trust/dealer 只 net call/put)
    opt_buy_call_yi: Mapped[float | None] = mapped_column(Float)
    opt_sell_call_yi: Mapped[float | None] = mapped_column(Float)
    opt_buy_put_yi: Mapped[float | None] = mapped_column(Float)
    opt_sell_put_yi: Mapped[float | None] = mapped_column(Float)

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
