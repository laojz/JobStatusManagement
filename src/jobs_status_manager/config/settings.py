"""Typed environment-backed application settings."""

from os import environ
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_PORT: Final = 1
MAX_PORT: Final = 65535
QQ_API_BASE_URL: Final = "https://api.bot.qq.com"
DEFAULT_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS: Final = 300
MAX_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS: Final = 3600
IMAP_HOST: Final = "imap.qq.com"
IMAP_PORT: Final = 993
IMAP_FOLDER: Final = "INBOX"
IMAP_MAX_BATCH_SIZE: Final = 500
LLM_MODEL: Final = "deepseek-flash"


class AppSettings(BaseSettings):
    """Application settings; external credentials remain optional in local mode."""

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
    runtime_mode: Literal["local", "production"] = "local"
    imap_enabled: bool = False
    imap_host: Literal["imap.qq.com"] = IMAP_HOST
    imap_port: int = IMAP_PORT
    imap_ssl: bool = True
    imap_folder: str = IMAP_FOLDER
    imap_account: str | None = None
    imap_auth_code: SecretStr | None = None
    imap_connect_timeout_seconds: Annotated[int, Field(gt=0)] = 10
    imap_command_timeout_seconds: Annotated[int, Field(gt=0)] = 30
    imap_batch_size: int = 100
    llm_enabled: bool = False
    llm_base_url: AnyHttpUrl | None = None
    llm_api_key: SecretStr | None = None
    llm_model: Literal["deepseek-flash"] = LLM_MODEL
    llm_connect_timeout_seconds: Annotated[int, Field(gt=0)] = 5
    llm_read_timeout_seconds: Annotated[int, Field(gt=0)] = 60
    llm_write_timeout_seconds: Annotated[int, Field(gt=0)] = 15
    llm_pool_timeout_seconds: Annotated[int, Field(gt=0)] = 10
    llm_max_connections: Annotated[int, Field(gt=0)] = 20
    llm_max_keepalive_connections: Annotated[int, Field(gt=0)] = 10
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
        "qq_user_openid",
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

    @field_validator("imap_folder")
    @classmethod
    def require_inbox_folder(cls, value: str) -> str:
        """Require the v1 read-only INBOX folder."""
        normalized = value.strip()
        if normalized != IMAP_FOLDER:
            message = f"must be {IMAP_FOLDER}"
            raise ValueError(message)
        return normalized

    @field_validator("imap_port", "imap_ssl")
    @classmethod
    def require_imap_transport(cls, value: int | bool, info: ValidationInfo) -> int | bool:
        """Require the v1 QQ IMAP port and SSL transport."""
        if info.field_name == "imap_port" and value != IMAP_PORT:
            message = f"must be {IMAP_PORT}"
            raise ValueError(message)
        if info.field_name == "imap_ssl" and not value:
            message = "must be true"
            raise ValueError(message)
        return value

    @field_validator(
        "imap_auth_code",
        "llm_api_key",
        "embedding_api_key",
        "qq_access_token",
        "qq_webhook_token",
        "qq_app_secret",
        mode="before",
    )
    @classmethod
    def normalize_external_secret(cls, value: SecretStr | str | None) -> SecretStr | None:
        """Treat blank external credentials as absent without exposing their values."""
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return None if not value.get_secret_value().strip() else value
        return None if not value.strip() else SecretStr(value)

    @field_validator("imap_account", "qq_app_id", mode="before")
    @classmethod
    def normalize_optional_identifier(cls, value: str | None) -> str | None:
        """Treat blank optional identifiers as absent and trim configured values."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("llm_base_url", "qq_token_base_url", mode="before")
    @classmethod
    def normalize_optional_url(cls, value: AnyHttpUrl | str | None) -> AnyHttpUrl | str | None:
        """Treat blank optional endpoints as absent before URL parsing."""
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("qq_api_base_url", "qq_token_base_url", "llm_base_url")
    @classmethod
    def require_https_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        """Require HTTPS for configured external endpoints."""
        if value is not None and value.scheme.casefold() != "https":
            message = "must use https"
            raise ValueError(message)
        return value

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
    def require_positive_limits(cls, value: int, info: ValidationInfo) -> int:
        """Reject non-positive agent safety limits."""
        if value <= 0:
            message = "must be greater than zero"
            raise ValueError(message)
        if (
            info.field_name == "qq_dispatch_timestamp_max_age_seconds"
            and value > MAX_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS
        ):
            message = f"must be at most {MAX_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS} seconds"
            raise ValueError(message)
        return value

    @field_validator("imap_batch_size")
    @classmethod
    def require_bounded_imap_batch_size(cls, value: int) -> int:
        """Keep each IMAP poll batch within the supported bound."""
        if not 1 <= value <= IMAP_MAX_BATCH_SIZE:
            message = f"must be between 1 and {IMAP_MAX_BATCH_SIZE}"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def require_capability_configuration(self) -> Self:
        """Require complete external capabilities and reject legacy configuration."""
        if any(name.casefold() == "app_imap_password" for name in environ):
            message = "APP_IMAP_PASSWORD is no longer supported; use APP_IMAP_AUTH_CODE"
            raise ValueError(message)
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
        if self.runtime_mode == "production" and not self.imap_enabled:
            message = "production requires imap_enabled"
            raise ValueError(message)
        if self.runtime_mode == "production" and not self.llm_enabled:
            message = "production requires llm_enabled"
            raise ValueError(message)
        if self.imap_enabled and (self.imap_account is None or self.imap_auth_code is None):
            message = "imap_enabled requires imap_account and imap_auth_code"
            raise ValueError(message)
        if self.llm_enabled and (self.llm_base_url is None or self.llm_api_key is None):
            message = "llm_enabled requires llm_base_url and llm_api_key"
            raise ValueError(message)
        if self.llm_max_keepalive_connections > self.llm_max_connections:
            message = "llm_max_keepalive_connections must not exceed llm_max_connections"
            raise ValueError(message)
        return self
