"""贊助 / 付費相關 ORM(Phase 1 合規基礎建設,不含金流邏輯)。

- CheckoutAgreement:結帳時記錄使用者同意過的條款版本 + IP/UA(防退款糾紛)
- UserPlan:會員方案狀態(free / premium / premium_until / total_paid)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CheckoutAgreement(Base):
    """結帳時記錄使用者同意服務條款的快照(防退款糾紛舉證)。"""
    __tablename__ = "checkout_agreements"
    __table_args__ = (
        Index("idx_checkout_agreements_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(32), nullable=False)
    agreed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    agreement_text: Mapped[Optional[str]] = mapped_column(Text)


class UserPlan(Base):
    """使用者目前方案(預設 free,贊助後變 premium 並含 premium_until)。"""
    __tablename__ = "user_plans"
    __table_args__ = (
        Index("idx_user_plans_premium_until", "premium_until"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), primary_key=True
    )
    current_plan: Mapped[str] = mapped_column(String(16), nullable=False, default="free")
    premium_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    total_paid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_payment_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
