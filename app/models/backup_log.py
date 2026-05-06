"""每次資料庫備份的執行紀錄 — 給後台 /admin/backup/status 監控用。

紀律 #20:備份是資料主權鐵律的一部分,每一次的成功/失敗、大小、耗時都要留紀錄,
讓後台一眼看出「最近一次成功備份」與「異常」。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BackupLog(Base):
    __tablename__ = "backup_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    backup_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )
    backup_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<BackupLog id={self.id} {self.backup_type} {self.status} "
            f"at={self.backup_at} size={self.file_size_bytes}>"
        )
