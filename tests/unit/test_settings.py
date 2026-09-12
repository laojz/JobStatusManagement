"""Settings and logging safety tests."""

from pathlib import Path
from shutil import copyfile

import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import SecretStr, ValidationError

from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.config.validation import safe_settings_error
from jobs_status_manager.infrastructure.adapters.fakes import FakeQQGateway
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


@pytest.mark.parametrize("blank_value", ["", "   ", "\t\n"])
def test_blank_legacy_qq_secrets_become_none(blank_value: str) -> None:
    settings = AppSettings(
        _env_file=None,
        database_path=Path("database.sqlite3"),
        data_dir=Path("data"),
        bootstrap_user_external_key="user-key",
        bootstrap_user_display_name="User",
        bootstrap_mail_provider="local",
        bootstrap_mail_account_key="account-key",
        bootstrap_mail_display_name="Mailbox",
        qq_user_openid="openid",
        qq_access_token=blank_value,
        qq_webhook_token=blank_value,
    )

    assert settings.qq_access_token is None
    assert settings.qq_webhook_token is None
    assert settings.qq_enabled is False


def test_nonblank_legacy_qq_secrets_remain_secret() -> None:
    values = {
        "_env_file": None,
        "database_path": Path("database.sqlite3"),
        "data_dir": Path("data"),
        "bootstrap_user_external_key": "user-key",
        "bootstrap_user_display_name": "User",
        "bootstrap_mail_provider": "local",
        "bootstrap_mail_account_key": "account-key",
        "bootstrap_mail_display_name": "Mailbox",
        "qq_user_openid": "openid",
        "qq_access_token": "access-token-placeholder",
        "qq_webhook_token": "webhook-token-placeholder",
    }
    settings = AppSettings(**values)

    assert settings.qq_access_token == SecretStr("access-token-placeholder")
    assert settings.qq_webhook_token == SecretStr("webhook-token-placeholder")


def test_credential_free_settings_preserve_explicit_fake_qq_injection() -> None:
    settings = AppSettings(
        _env_file=None,
        database_path=Path("database.sqlite3"),
        data_dir=Path("data"),
        bootstrap_user_external_key="user-key",
        bootstrap_user_display_name="User",
        bootstrap_mail_provider="local",
        bootstrap_mail_account_key="account-key",
        bootstrap_mail_display_name="Mailbox",
        qq_user_openid="openid",
    )
    fake = FakeQQGateway()

    result = fake.push(settings.qq_user_openid, "local demo")

    assert settings.qq_access_token is None
    assert settings.qq_webhook_token is None
    assert settings.qq_token_base_url is None
    assert result.success
    assert fake.calls == [("push", ("openid", "local demo"))]


@pytest.mark.parametrize("blank_token_url", ["", " \t "], ids=["empty", "whitespace"])
def test_env_example_loads_without_qq_credentials_and_blank_token_url(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    blank_token_url: str,
) -> None:
    env_file = tmp_path / ".env"
    copyfile(Path(__file__).resolve().parents[2] / ".env.example", env_file)
    env_file.write_text(
        env_file.read_text().replace(
            "APP_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS=300",
            "APP_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS=45",
        )
    )
    for line in env_file.read_text().splitlines():
        if line.startswith("APP_"):
            monkeypatch.delenv(line.partition("=")[0], raising=False)
    if blank_token_url:
        monkeypatch.setenv("APP_QQ_TOKEN_BASE_URL", blank_token_url)

    settings = AppSettings(_env_file=env_file)

    assert settings.qq_app_id is None
    assert settings.qq_app_secret is None
    assert settings.qq_token_base_url is None
    assert settings.qq_dispatch_timestamp_max_age_seconds == 45


@pytest.mark.parametrize(
    ("credential_field", "credential_value"),
    [("qq_app_id", "app-id-placeholder"), ("qq_app_secret", "app-secret-placeholder")],
)
def test_partial_qq_credentials_fail_without_exposing_values(
    credential_field: str,
    credential_value: str,
) -> None:
    values = {
        "_env_file": None,
        "database_path": Path("database.sqlite3"),
        "data_dir": Path("data"),
        "bootstrap_user_external_key": "user-key",
        "bootstrap_user_display_name": "User",
        "bootstrap_mail_provider": "local",
        "bootstrap_mail_account_key": "account-key",
        "bootstrap_mail_display_name": "Mailbox",
        "qq_user_openid": "openid",
        credential_field: credential_value,
    }

    with pytest.raises(ValidationError) as raised:
        AppSettings(**values)

    assert any("must be configured together" in item["msg"] for item in raised.value.errors())
    safe_error = safe_settings_error(raised.value)
    assert safe_error == "invalid configuration fields: configuration"
    assert credential_value not in safe_error


def test_complete_qq_configuration_accepts_explicit_overrides() -> None:
    values = {
        "_env_file": None,
        "database_path": Path("database.sqlite3"),
        "data_dir": Path("data"),
        "bootstrap_user_external_key": "user-key",
        "bootstrap_user_display_name": "User",
        "bootstrap_mail_provider": "local",
        "bootstrap_mail_account_key": "account-key",
        "bootstrap_mail_display_name": "Mailbox",
        "qq_user_openid": "openid",
        "qq_app_id": "app-id-placeholder",
        "qq_app_secret": "app-secret-placeholder",
        "qq_enabled": True,
        "qq_api_base_url": "https://api.example.invalid/",
        "qq_token_base_url": "https://token.example.invalid/",
        "qq_webhook_path": "/webhooks/custom-qq",
        "qq_request_timeout_seconds": 15,
        "qq_dispatch_timestamp_max_age_seconds": 45,
        "qq_download_timeout_seconds": 30,
        "qq_max_download_bytes": 2_000_000,
    }
    settings = AppSettings(**values)

    assert settings.qq_app_id == "app-id-placeholder"
    assert settings.qq_app_secret == SecretStr("app-secret-placeholder")
    assert str(settings.qq_api_base_url) == "https://api.example.invalid/"
    assert str(settings.qq_token_base_url) == "https://token.example.invalid/"
    assert settings.qq_webhook_path == "/webhooks/custom-qq"
    assert settings.qq_request_timeout_seconds == 15
    assert settings.qq_dispatch_timestamp_max_age_seconds == 45
    assert settings.qq_download_timeout_seconds == 30
    assert settings.qq_max_download_bytes == 2_000_000


@pytest.mark.parametrize("value", [0, -1])
def test_dispatch_timestamp_max_age_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError, match="greater than zero"):
        AppSettings(
            _env_file=None,
            database_path=Path("database.sqlite3"),
            data_dir=Path("data"),
            bootstrap_user_external_key="user-key",
            bootstrap_user_display_name="User",
            bootstrap_mail_provider="local",
            bootstrap_mail_account_key="account-key",
            bootstrap_mail_display_name="Mailbox",
            qq_user_openid="openid",
            qq_dispatch_timestamp_max_age_seconds=value,
        )


def test_dispatch_timestamp_max_age_has_a_safe_upper_bound() -> None:
    with pytest.raises(ValidationError, match="at most"):
        AppSettings(
            _env_file=None,
            database_path=Path("database.sqlite3"),
            data_dir=Path("data"),
            bootstrap_user_external_key="user-key",
            bootstrap_user_display_name="User",
            bootstrap_mail_provider="local",
            bootstrap_mail_account_key="account-key",
            bootstrap_mail_display_name="Mailbox",
            qq_user_openid="openid",
            qq_dispatch_timestamp_max_age_seconds=3601,
        )


@pytest.mark.parametrize("blank_token_url", [None, " \t "], ids=["missing", "blank"])
def test_complete_qq_configuration_requires_explicit_token_url(blank_token_url: str | None) -> None:
    values = {
        "_env_file": None,
        "database_path": Path("database.sqlite3"),
        "data_dir": Path("data"),
        "bootstrap_user_external_key": "user-key",
        "bootstrap_user_display_name": "User",
        "bootstrap_mail_provider": "local",
        "bootstrap_mail_account_key": "account-key",
        "bootstrap_mail_display_name": "Mailbox",
        "qq_user_openid": "openid",
        "qq_app_id": "app-id-placeholder",
        "qq_app_secret": "app-secret-placeholder",
        "qq_token_base_url": blank_token_url,
    }

    with pytest.raises(ValidationError) as raised:
        AppSettings(**values)

    safe_error = safe_settings_error(raised.value)
    assert safe_error == "invalid configuration fields: configuration"
    assert "app-secret-placeholder" not in safe_error


def test_qq_enabled_requires_complete_credentials() -> None:
    with pytest.raises(ValidationError, match="qq_enabled requires"):
        AppSettings(
            _env_file=None,
            database_path=Path("database.sqlite3"),
            data_dir=Path("data"),
            bootstrap_user_external_key="user-key",
            bootstrap_user_display_name="User",
            bootstrap_mail_provider="local",
            bootstrap_mail_account_key="account-key",
            bootstrap_mail_display_name="Mailbox",
            qq_user_openid="openid",
            qq_enabled=True,
        )


def test_malformed_qq_token_url_fails_without_exposing_secret() -> None:
    values = {
        "_env_file": None,
        "database_path": Path("database.sqlite3"),
        "data_dir": Path("data"),
        "bootstrap_user_external_key": "user-key",
        "bootstrap_user_display_name": "User",
        "bootstrap_mail_provider": "local",
        "bootstrap_mail_account_key": "account-key",
        "bootstrap_mail_display_name": "Mailbox",
        "qq_user_openid": "openid",
        "qq_app_id": "app-id-placeholder",
        "qq_app_secret": "app-secret-placeholder",
        "qq_token_base_url": "not-a-url",
    }

    with pytest.raises(ValidationError) as raised:
        AppSettings(**values)

    safe_error = safe_settings_error(raised.value)
    assert "qq_token_base_url" in safe_error
    assert "app-secret-placeholder" not in safe_error


def test_legacy_token_url_is_explicitly_selectable_but_not_default() -> None:
    values = {
        "_env_file": None,
        "database_path": Path("database.sqlite3"),
        "data_dir": Path("data"),
        "bootstrap_user_external_key": "user-key",
        "bootstrap_user_display_name": "User",
        "bootstrap_mail_provider": "local",
        "bootstrap_mail_account_key": "account-key",
        "bootstrap_mail_display_name": "Mailbox",
        "qq_user_openid": "openid",
        "qq_app_id": "app-id-placeholder",
        "qq_app_secret": "app-secret-placeholder",
        "qq_token_base_url": "https://bots.qq.com",
    }
    settings = AppSettings(**values)

    assert str(settings.qq_token_base_url) == "https://bots.qq.com/"


@pytest.mark.parametrize("field", ["qq_api_base_url", "qq_token_base_url"])
def test_qq_production_urls_reject_http(field: str) -> None:
    values = {
        "_env_file": None,
        "database_path": Path("database.sqlite3"),
        "data_dir": Path("data"),
        "bootstrap_user_external_key": "user-key",
        "bootstrap_user_display_name": "User",
        "bootstrap_mail_provider": "local",
        "bootstrap_mail_account_key": "account-key",
        "bootstrap_mail_display_name": "Mailbox",
        "qq_user_openid": "openid",
        "qq_app_id": "app-id-placeholder",
        "qq_app_secret": "app-secret-placeholder",
        "qq_token_base_url": "https://token.example.invalid",
        field: "http://api.example.invalid",
    }

    with pytest.raises(ValidationError, match="must use https"):
        AppSettings(**values)
