"""Settings and logging safety tests."""

from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import SecretStr, ValidationError

from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.config.validation import safe_settings_error
from jobs_status_manager.infrastructure.logging import redact_event


def test_settings_require_local_identity_fields(monkeypatch: MonkeyPatch) -> None:
    """Missing required values expose field names only."""
    for name in (
        "APP_DATABASE_PATH",
        "APP_DATA_DIR",
        "APP_BOOTSTRAP_USER_EXTERNAL_KEY",
        "APP_BOOTSTRAP_USER_DISPLAY_NAME",
        "APP_BOOTSTRAP_MAIL_PROVIDER",
        "APP_BOOTSTRAP_MAIL_ACCOUNT_KEY",
        "APP_BOOTSTRAP_MAIL_DISPLAY_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    try:
        AppSettings(_env_file=None)
    except ValidationError as error:
        message = safe_settings_error(error)
    else:
        message = "settings unexpectedly valid"
        raise AssertionError(message)
    assert "database_path" in message
    assert "bootstrap_user_display_name" in message
    assert "secret-value" not in message


def test_secret_like_log_keys_are_redacted() -> None:
    """Secret-like keys are replaced while ordinary fields remain."""
    result = redact_event("", "", {"api_token": "secret-value", "phase": "0"})
    assert result == {"api_token": "[REDACTED]", "phase": "0"}


@pytest.mark.parametrize("blank_key", ["", "   ", "\t\n"])
def test_embedding_key_blank_values_become_none(blank_key: str) -> None:
    settings = AppSettings(
        _env_file=None,
        database_path=Path("database.sqlite3"),
        data_dir=Path("data"),
        bootstrap_user_external_key="user-key",
        bootstrap_user_display_name="User",
        bootstrap_mail_provider="imap",
        bootstrap_mail_account_key="account-key",
        bootstrap_mail_display_name="Mailbox",
        qq_user_openid="openid",
        embedding_api_key=blank_key,
    )

    assert settings.embedding_api_key is None


def test_embedding_key_remains_secret_when_configured() -> None:
    settings = AppSettings(
        _env_file=None,
        database_path=Path("database.sqlite3"),
        data_dir=Path("data"),
        bootstrap_user_external_key="user-key",
        bootstrap_user_display_name="User",
        bootstrap_mail_provider="imap",
        bootstrap_mail_account_key="account-key",
        bootstrap_mail_display_name="Mailbox",
        qq_user_openid="openid",
        embedding_api_key="secret-value",
    )

    assert settings.embedding_api_key == SecretStr("secret-value")
