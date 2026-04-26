"""ETF master list model."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.kbar import DailyKBar


class ETF(Base):
    __tablename__ = "etf_list"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_tracked: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    kbars: Mapped[list["DailyKBar"]] = relationship(
        back_populates="etf", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ETF {self.code} {self.name}>"
