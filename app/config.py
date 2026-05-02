"""Application settings loaded from environment / .env file via Pydantic v2."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Central settings object — read once at startup, inject elsewhere."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "ETF 觀察室"
    app_brand_en: str = "ETF Watch"
    app_brand_full: str = "ETF 觀察室 · ETF Watch"
    app_env: str = Field(default="dev", description="dev / prod")
    debug: bool = True

    host: str = "127.0.0.1"
    port: int = 8000

    database_url: str = Field(
        default=f"sqlite:///{(DATA_DIR / 'etf.db').as_posix()}",
        description="SQLAlchemy DB URL — SQLite single file by default.",
    )

    scheduler_timezone: str = "Asia/Taipei"
    daily_fetch_hour: int = 14
    daily_fetch_minute: int = 30

    finmind_api_token: str | None = Field(
        default=None,
        description="FinMind API token — 免費版可空,填了拿較高 rate limit",
    )

    telegram_bot_token: str | None = None
    telegram_admin_chat_id: str | None = None

    secret_key: str = "change-me-in-production"

    # 後台 /admin/login 密碼。占位值 — 部署前請改 env。
    admin_password: str = Field(default="CHANGE_ME", description="ADMIN_PASSWORD env var,後台登入用")

    # === Google OAuth(Step 6 會員系統)===
    # 三個都從 .env / Zeabur env 讀,空字串 = OAuth 功能未啟用,登入按鈕不顯示
    google_client_id: str = Field(default="", description="Google OAuth Client ID")
    google_client_secret: str = Field(default="", description="Google OAuth Client Secret")
    session_secret_key: str = Field(
        default="dev-only-change-me-32-chars-minimum-xxx",
        description="Starlette SessionMiddleware 簽章 key,production 必填 ≥ 32 字元 random",
    )

    # Analytics 高 session IP 排除門檻 — 24h 內同 IP 開 ≥ N 個 session 視為
    # 自動化掃描器,從 DAU/PV/排行統計排除(不擋訪問,只排除計算)。
    high_session_threshold: int = Field(default=15, description="24h 內同 IP session 數上限,超過自動排除統計")


settings = Settings()
