"""Typed environment-backed application settings."""

from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_PORT: Final = 1
MAX_PORT: Final = 65535
QQ_API_BASE_URL: Final = "https://api.bot.qq.com"
DEFAULT_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS: Final = 300
MAX_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS: Final = 3600


class AppSettings(BaseSettings):
    """Phase 0 settings; external credentials remain optional."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=False,
        extra="forbid",
        case_sensitive=False,
    )

    database_path: Path
    data_dir: Path
    bootstrap_user_external_key: str
    bootstrap_user_display_name: str
    bootstrap_mail_provider: str
    bootstrap_mail_account_key: str
    bootstrap_mail_display_name: str
    bootstrap_mail_credential_ref: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    llm_api_key: SecretStr | None = None
    qq_enabled: bool = False
    qq_access_token: SecretStr | None = None
    qq_app_secret: SecretStr | None = None
    qq_app_id: str | None = None
    qq_api_base_url: Annotated[AnyHttpUrl, Field(default=QQ_API_BASE_URL)]
    qq_token_base_url: Annotated[AnyHttpUrl | None, Field(default=None)]
    qq_webhook_path: str = "/webhooks/qq"
    qq_request_timeout_seconds: int = 10
    qq_dispatch_timestamp_max_age_seconds: int = DEFAULT_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS
    qq_download_timeout_seconds: int = 30
    qq_download_deadline_seconds: int = 30
    qq_max_download_bytes: int = 10_485_760
    imap_password: SecretStr | None = None
    embedding_api_key: SecretStr | None = None
    chroma_path: Path | None = None
    qq_webhook_token: SecretStr | None = None
    qq_user_openid: str
    agent_max_tool_calls: int = 8
    agent_max_run_seconds: int = 120
    agent_tool_timeout_seconds: int = 10
    agent_max_result_chars: int = 12000
    agent_max_delivery_attempts: int = 3
    max_upload_bytes: int = 10_485_760

    @classmethod
    def from_environment(cls) -> "AppSettings":
        """Load settings from environment and dotenv sources."""
        return cls()

    @field_validator(
        "bootstrap_user_external_key",
        "bootstrap_user_display_name",
        "bootstrap_mail_provider",
        "bootstrap_mail_account_key",
        "bootstrap_mail_display_name",
    )
    @classmethod
    def require_nonempty(cls, value: str) -> str:
        """Reject blank identity fields without exposing their values."""
        normalized = value.strip()
        if not normalized:
            message = "must not be empty"
            raise ValueError(message)
        return normalized

    @field_validator("port")
    @classmethod
    def require_valid_port(cls, value: int) -> int:
        """Require a TCP port in the valid range."""
        if not MIN_PORT <= value <= MAX_PORT:
            message = f"must be between {MIN_PORT} and {MAX_PORT}"
            raise ValueError(message)
        return value

    @field_validator("embedding_api_key", mode="before")
    @classmethod
    def normalize_embedding_api_key(cls, value: SecretStr | str | None) -> SecretStr | None:
        """Treat blank embedding credentials as absent without exposing them."""
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return None if not value.get_secret_value().strip() else value
        return None if not value.strip() else SecretStr(value)

    @field_validator("qq_access_token", "qq_webhook_token", mode="before")
    @classmethod
    def normalize_legacy_qq_secret(cls, value: SecretStr | str | None) -> SecretStr | None:
        """Treat blank legacy QQ credentials as absent without exposing them."""
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return None if not value.get_secret_value().strip() else value
        return None if not value.strip() else SecretStr(value)

    @field_validator("qq_app_id", mode="before")
    @classmethod
    def normalize_qq_app_id(cls, value: str | None) -> str | None:
        """Treat blank QQ application IDs as absent."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("qq_app_secret", mode="before")
    @classmethod
    def normalize_qq_app_secret(cls, value: SecretStr | str | None) -> SecretStr | None:
        """Treat blank QQ application secrets as absent without exposing them."""
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return None if not value.get_secret_value().strip() else value
        return None if not value.strip() else SecretStr(value)

    @field_validator("qq_token_base_url", mode="before")
    @classmethod
    def normalize_qq_token_base_url(cls, value: AnyHttpUrl | str | None) -> AnyHttpUrl | str | None:
        """Treat blank QQ token endpoints as absent before URL parsing."""
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("qq_api_base_url", "qq_token_base_url")
    @classmethod
    def require_https_qq_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        """Require HTTPS for configured QQ API endpoints."""
        if value is not None and value.scheme.casefold() != "https":
            message = "must use https"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def require_qq_credentials_together(self) -> Self:
        """Require complete QQ credentials and an explicit token endpoint."""
        if (self.qq_app_id is None) != (self.qq_app_secret is None):
            message = "qq_app_id and qq_app_secret must be configured together"
            raise ValueError(message)
        if self.qq_app_id is not None and self.qq_token_base_url is None:
            message = "qq_token_base_url is required when QQ credentials are configured"
            raise ValueError(message)
        if self.qq_enabled and (
            self.qq_app_id is None or self.qq_app_secret is None or self.qq_token_base_url is None
        ):
            message = "qq_enabled requires complete QQ credentials and an explicit token URL"
            raise ValueError(message)
        return self

    @field_validator("qq_webhook_path")
    @classmethod
    def require_qq_webhook_path(cls, value: str) -> str:
        """Require an absolute webhook route path."""
        normalized = value.strip()
        if not normalized.startswith("/"):
            message = "must start with /"
            raise ValueError(message)
        return normalized

    @field_validator(
        "agent_max_tool_calls",
        "agent_max_run_seconds",
        "agent_tool_timeout_seconds",
        "agent_max_result_chars",
        "agent_max_delivery_attempts",
        "max_upload_bytes",
        "qq_request_timeout_seconds",
        "qq_dispatch_timestamp_max_age_seconds",
        "qq_download_timeout_seconds",
        "qq_download_deadline_seconds",
        "qq_max_download_bytes",
    )
    @classmethod
    def require_positive_limits(cls, value: int) -> int:
        """Reject non-positive agent safety limits."""
        if value <= 0:
            message = "must be greater than zero"
            raise ValueError(message)
        return value

    @field_validator("qq_dispatch_timestamp_max_age_seconds")
    @classmethod
    def require_bounded_dispatch_timestamp_age(cls, value: int) -> int:
        """Keep the accepted signed-dispatch window bounded."""
        if value > MAX_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS:
            message = f"must be at most {MAX_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS} seconds"
            raise ValueError(message)
        return value

    @field_validator("qq_user_openid")
    @classmethod
    def require_qq_sender(cls, value: str) -> str:
        """Require the single configured QQ sender identity."""
        normalized = value.strip()
        if not normalized:
            message = "must not be empty"
            raise ValueError(message)
        return normalized
