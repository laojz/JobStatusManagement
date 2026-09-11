"""Typed environment-backed application settings."""

from pathlib import Path
from typing import Final

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_PORT: Final = 1
MAX_PORT: Final = 65535


class AppSettings(BaseSettings):
    """Phase 0 settings; external credentials remain optional."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
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
    qq_access_token: SecretStr | None = None
    imap_password: SecretStr | None = None
    embedding_api_key: SecretStr | None = None
    chroma_path: Path | None = None
    qq_webhook_token: SecretStr | None = None
    qq_user_openid: str
    agent_max_tool_calls: int = 8
    agent_max_run_seconds: int = 120
    agent_tool_timeout_seconds: int = 10
    agent_max_result_chars: int = 12000
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

    @field_validator(
        "agent_max_tool_calls",
        "agent_max_run_seconds",
        "agent_tool_timeout_seconds",
        "agent_max_result_chars",
        "max_upload_bytes",
    )
    @classmethod
    def require_positive_limits(cls, value: int) -> int:
        """Reject non-positive agent safety limits."""
        if value <= 0:
            message = "must be greater than zero"
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
