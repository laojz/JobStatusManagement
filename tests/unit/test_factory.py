from pathlib import Path
from typing import Never

import pytest
from pydantic import SecretStr

from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.factory import (
    AdapterConfigurationError,
    OwnedAdapters,
    create_owned_adapters,
)


def _settings(
    *, embedding_api_key: SecretStr | None = None, chroma_path: Path | None = None
) -> AppSettings:
    return AppSettings(
        _env_file=None,
        database_path=Path("database.sqlite3"),
        data_dir=Path("data"),
        bootstrap_user_external_key="user-key",
        bootstrap_user_display_name="User",
        bootstrap_mail_provider="imap",
        bootstrap_mail_account_key="account-key",
        bootstrap_mail_display_name="Mailbox",
        qq_user_openid="openid",
        embedding_api_key=embedding_api_key,
        chroma_path=chroma_path,
    )


def test_factory_without_key_skips_adapters_even_with_chroma_path(
    tmp_path: Path,
) -> None:
    assert create_owned_adapters(_settings(chroma_path=tmp_path)) is None


def test_factory_with_key_without_chroma_path_raises() -> None:
    with pytest.raises(AdapterConfigurationError):
        create_owned_adapters(_settings(embedding_api_key=SecretStr("secret-value")))


def test_factory_closes_embedding_when_chroma_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    close_calls = 0

    class FakeEmbedding:
        def __init__(self, api_key: SecretStr) -> None:
            pass

        def close(self) -> None:
            nonlocal close_calls
            close_calls += 1

    def fail_chroma(path: Path) -> Never:
        message = "chroma construction failed"
        raise RuntimeError(message)

    monkeypatch.setattr(
        "jobs_status_manager.infrastructure.adapters.factory.BailianEmbedding",
        FakeEmbedding,
    )
    monkeypatch.setattr(
        "jobs_status_manager.infrastructure.adapters.factory.LocalChroma",
        fail_chroma,
    )

    with pytest.raises(RuntimeError, match="chroma construction failed"):
        create_owned_adapters(
            _settings(
                embedding_api_key=SecretStr("secret-value"),
                chroma_path=tmp_path,
            )
        )

    assert close_calls == 1


def test_factory_returns_owned_adapters_that_close_provider_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    close_calls = 0

    class FakeEmbedding:
        def __init__(self, api_key: SecretStr) -> None:
            pass

        def close(self) -> None:
            nonlocal close_calls
            close_calls += 1

    class FakeChroma:
        def __init__(self, path: Path) -> None:
            pass

    monkeypatch.setattr(
        "jobs_status_manager.infrastructure.adapters.factory.BailianEmbedding",
        FakeEmbedding,
    )
    monkeypatch.setattr(
        "jobs_status_manager.infrastructure.adapters.factory.LocalChroma",
        FakeChroma,
    )

    resources = create_owned_adapters(
        _settings(
            embedding_api_key=SecretStr("secret-value"),
            chroma_path=tmp_path,
        )
    )

    assert isinstance(resources, OwnedAdapters)
    assert resources.embedding is resources._embedding_client
    resources.close()
    assert close_calls == 1


def test_factory_preserves_construction_error_when_embedding_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeEmbedding:
        def __init__(self, api_key: SecretStr) -> None:
            pass

        def close(self) -> None:
            message = "cleanup-secret-value"
            raise RuntimeError(message)

    def fail_chroma(path: Path) -> Never:
        message = "construction-secret-value"
        raise RuntimeError(message)

    monkeypatch.setattr(
        "jobs_status_manager.infrastructure.adapters.factory.BailianEmbedding",
        FakeEmbedding,
    )
    monkeypatch.setattr(
        "jobs_status_manager.infrastructure.adapters.factory.LocalChroma",
        fail_chroma,
    )

    with pytest.raises(RuntimeError, match="construction-secret-value") as raised:
        create_owned_adapters(
            _settings(
                embedding_api_key=SecretStr("secret-value"),
                chroma_path=tmp_path,
            )
        )

    assert str(raised.value) == "construction-secret-value"
    assert "cleanup-secret-value" not in str(raised.value)
