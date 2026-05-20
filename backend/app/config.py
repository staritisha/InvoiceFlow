# ═══════════════════════════════════════════════════════════════════════════════
#  AI Invoice Intelligence Platform — config.py
#  Centralized environment-driven configuration management supporting AI
#  provider abstraction, workflow orchestration, real-time infrastructure,
#  and modular feature enablement.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── SaaS Branding ─────────────────────────────────────────────────────────

    app_name: str = "AI Invoice Intelligence Platform"
    app_version: str = "2.0.0"
    company_name: str = "InvoiceFlow AI"
    support_email: str = "support@invoiceflow.ai"

    # ── Environment ───────────────────────────────────────────────────────────

    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    # ── Database ──────────────────────────────────────────────────────────────

    database_url: str = "sqlite:///./invoiceflow.db"

    # Connection pool settings (PostgreSQL)
    db_pool_size: int = 20
    db_max_overflow: int = 40
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    # ── Security / JWT ────────────────────────────────────────────────────────

    secret_key: str = "changeme-use-a-long-random-string-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    @field_validator("secret_key")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        if v == "changeme-use-a-long-random-string-in-production":
            return v  # allow default in development
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        return v
    # ── Uppercase aliases (backward-compat with existing code) ────────────────
    @property
    def SECRET_KEY(self) -> str:
        return self.secret_key
    @property
    def DATABASE_URL(self) -> str:
        return self.database_url
    @property
    def ALGORITHM(self) -> str:
        return self.algorithm
    @property
    def ACCESS_TOKEN_EXPIRE_MINUTES(self) -> int:
        return self.access_token_expire_minutes
    @property
    def OPENAI_API_KEY(self) -> Optional[str]:
        return self.openai_api_key
    # ── AI Provider ───────────────────────────────────────────────────────────

    ai_provider: Literal["openai", "claude", "gemini", "deepseek"] = "openai"
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None

    ai_model: str = "gpt-4.1"
    ai_temperature: float = 0.3
    ai_max_tokens: int = 4000
    ai_timeout: int = 60

    # ── AI Feature Flags ──────────────────────────────────────────────────────

    enable_ai: bool = True
    enable_ai_insights: bool = True
    enable_ai_chat: bool = True
    enable_voice_ai: bool = True
    enable_experimental_ai: bool = False

    # ── Feature Flags ─────────────────────────────────────────────────────────

    enable_analytics: bool = True
    enable_reports: bool = True
    enable_workflows: bool = True
    enable_notifications: bool = True
    enable_voice_commands: bool = True
    enable_websockets: bool = True
    enable_scheduler: bool = True

    # ── Demo Mode ─────────────────────────────────────────────────────────────

    enable_demo_mode: bool = False
    demo_user_email: str = "demo@invoiceflow.ai"
    demo_user_password: str = "demo1234"

    # ── CORS ──────────────────────────────────────────────────────────────────

    allowed_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
    ]

    # ── Rate Limiting ─────────────────────────────────────────────────────────

    rate_limit_per_minute: int = 120
    ai_rate_limit_per_hour: int = 500

    # ── Redis ─────────────────────────────────────────────────────────────────

    redis_url: Optional[str] = None
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None

    @property
    def redis_dsn(self) -> str:
        if self.redis_url:
            return self.redis_url
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ── Email / SMTP ──────────────────────────────────────────────────────────

    email_host: Optional[str] = None
    email_port: int = 587
    email_username: Optional[str] = None
    email_password: Optional[str] = None
    email_from: str = "noreply@invoiceflow.ai"
    email_use_tls: bool = True

    # ── Stripe ────────────────────────────────────────────────────────────────

    stripe_secret_key: Optional[str] = None
    stripe_publishable_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None

    # ── File Uploads ──────────────────────────────────────────────────────────

    max_file_size_mb: int = 25
    allowed_extensions: List[str] = ["pdf", "png", "jpg", "jpeg", "csv", "xlsx"]
    upload_storage_path: str = "app/static/uploads"

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    # ── Voice / Audio ─────────────────────────────────────────────────────────

    voice_provider: str = "whisper"
    max_audio_seconds: int = 120
    supported_audio_formats: List[str] = ["mp3", "wav", "m4a", "ogg", "webm"]

    # ── Multi-Currency ────────────────────────────────────────────────────────

    default_currency: str = "USD"
    supported_currencies: List[str] = ["USD", "EUR", "INR", "GBP", "AED", "SGD", "CAD", "AUD"]
    auto_exchange_rate_refresh: bool = True

    # ── Report Exports ────────────────────────────────────────────────────────

    pdf_export_enabled: bool = True
    excel_export_enabled: bool = True
    csv_export_enabled: bool = True
    report_storage_path: str = "app/static/generated_reports"

    # ── Scheduler ─────────────────────────────────────────────────────────────

    reminder_check_interval: int = 300       # seconds
    workflow_interval: int = 60              # seconds
    analytics_refresh_interval: int = 1800  # seconds

    # ── WebSockets ────────────────────────────────────────────────────────────

    websocket_heartbeat_interval: int = 30  # seconds
    max_websocket_connections: int = 1000

    # ── Logging ───────────────────────────────────────────────────────────────

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    enable_request_logging: bool = True
    enable_ai_logging: bool = True
    enable_error_tracking: bool = True

    # ── Cross-field Validation ────────────────────────────────────────────────

    @model_validator(mode="after")
    def warn_missing_keys(self) -> "Settings":
        import logging
        _log = logging.getLogger("invoiceflow.config")

        if self.enable_ai and not self._active_ai_key():
            _log.warning(
                "ENABLE_AI=True but no AI provider API key is set. "
                "Set OPENAI_API_KEY (or the key for your chosen provider)."
            )
        if self.environment == "production" and self.debug:
            _log.warning("DEBUG=True in production — consider setting DEBUG=False.")
        if self.environment == "production" and not self.stripe_secret_key:
            _log.warning("STRIPE_SECRET_KEY is not set in production.")
        return self

    def _active_ai_key(self) -> Optional[str]:
        mapping = {
            "openai": self.openai_api_key,
            "claude": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
        }
        return mapping.get(self.ai_provider)

    # ── Convenience Helpers ───────────────────────────────────────────────────

    @property
    def active_ai_key(self) -> Optional[str]:
        """Return the API key for the currently configured AI provider."""
        return self._active_ai_key()

    @property
    def docs_enabled(self) -> bool:
        return not self.is_production

    def summary(self) -> dict:
        """Return a non-sensitive config summary suitable for /system/info."""
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "environment": self.environment,
            "debug": self.debug,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "feature_flags": {
                "ai": self.enable_ai,
                "ai_insights": self.enable_ai_insights,
                "ai_chat": self.enable_ai_chat,
                "voice_ai": self.enable_voice_ai,
                "analytics": self.enable_analytics,
                "reports": self.enable_reports,
                "workflows": self.enable_workflows,
                "notifications": self.enable_notifications,
                "voice_commands": self.enable_voice_commands,
                "websockets": self.enable_websockets,
                "scheduler": self.enable_scheduler,
                "demo_mode": self.enable_demo_mode,
                "experimental_ai": self.enable_experimental_ai,
            },
            "rate_limits": {
                "requests_per_minute": self.rate_limit_per_minute,
                "ai_requests_per_hour": self.ai_rate_limit_per_hour,
            },
            "upload_limits": {
                "max_file_size_mb": self.max_file_size_mb,
                "allowed_extensions": self.allowed_extensions,
            },
            "currency": {
                "default": self.default_currency,
                "supported": self.supported_currencies,
            },
            "voice": {
                "provider": self.voice_provider,
                "max_audio_seconds": self.max_audio_seconds,
                "supported_formats": self.supported_audio_formats,
            },
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

settings = Settings()
