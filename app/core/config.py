from pathlib import Path
from typing import List, Optional
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application settings
    APP_NAME: str = "MSG Incoming - Selective Ingestor & Router"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="development", description="development | production | testing")
    DEBUG: bool = Field(default=False)
    HOST: str = Field(default="0.0.0.0")
    PORT: int = Field(default=8001)

    # Security & JWT Auth
    SECRET_KEY: str = Field(
        default="super-secret-jwt-key-change-in-production-32b-length!",
        description="Secret key for JWT generation and verification",
    )
    ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60 * 24 * 7, description="7 days token lifetime")
    DEFAULT_ADMIN_EMAIL: str = Field(default="admin@quant.com")
    DEFAULT_ADMIN_PASSWORD: str = Field(default="Admin123456!")

    # PostgreSQL Database Connection
    POSTGRES_USER: str = Field(default="admin")
    POSTGRES_PASSWORD: str = Field(default="1234")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)
    POSTGRES_DB: str = Field(default="quant_trading")
    DATABASE_URL: Optional[str] = Field(
        default=None,
        description="Override for database URL. If omitted, built dynamically.",
    )

    # Telethon (Telegram MTProto) Settings via Environment Variables
    TELEGRAM_API_ID: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_API_ID", "API_ID", "telegram_api_id", "api_id"),
        description="Telegram App API ID from my.telegram.org",
    )
    TELEGRAM_API_HASH: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_API_HASH", "API_HASH", "telegram_api_hash", "api_hash"),
        description="Telegram App API Hash from my.telegram.org",
    )
    TELEGRAM_BOT_TOKEN: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "telegram_bot_token", "bot_token"),
        description="Optional Bot Token for Bot API MTProto sessions",
    )
    TELEGRAM_SESSION_NAME: str = Field(default="msg_ingestor", description="File name for Telethon session")
    TELEGRAM_SESSION_STRING: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_SESSION_STRING", "SESSION_STRING", "telegram_session_string", "session_string"),
        description="StringSession auth key for container environments without file mounts",
    )
    TELEGRAM_SESSIONS_DIR: str = Field(default="./sessions", description="Directory for persistent .session files")
    TELEGRAM_AUTO_START: bool = Field(default=True, description="Attempt client start on app lifespan boot if API credentials exist")
    TELEGRAM_OWNER_EMAIL: Optional[str] = Field(
        default=None,
        description="Optional owner email to scope Telethon messages to a single active user (Phase 1 multi-user)",
    )

    # Downstream Broker Execution & Dispatch Pipeline
    BROKER_API_URL: str = Field(
        default="http://localhost:8000",
        description="Endpoint for metatrader5-quant-server or execution engine",
    )
    BROKER_API_KEY: Optional[str] = Field(default=None, description="Optional X-API-Key for broker service auth")
    INTERNAL_API_KEY: Optional[str] = Field(
        default=None,
        description="Shared internal service key (X-Internal-Key) for quant operations ingest",
    )
    DISPATCH_TIMEOUT_SECONDS: float = Field(default=5.0, description="HTTP timeout for dispatching signals to brokers")
    RUST_APP_URL: Optional[str] = Field(default="http://localhost:3000", description="Optional Rust web server endpoint")

    # Webhook & Ingestor Verification
    WEBHOOK_SECRET: Optional[str] = Field(default=None, description="Optional shared secret for external webhooks")

    # CORS
    CORS_ORIGINS: List[str] = Field(default=["*"])

    @property
    def async_database_url(self) -> str:
        """
        Builds or formats the async PostgreSQL connection string with asyncpg driver.
        """
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgres://"):
                return url.replace("postgres://", "postgresql+asyncpg://", 1)
            return url
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_database_url(self) -> str:
        """
        Builds or formats the synchronous PostgreSQL connection string (useful for migrations).
        """
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if "+asyncpg" in url:
                return url.replace("+asyncpg", "", 1)
            return url
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
