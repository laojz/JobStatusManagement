from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobs_status_manager.bootstrap import cli_operations
from jobs_status_manager.bootstrap.cli import app
from jobs_status_manager.config.settings import AppSettings


def test_demo_mail_pipeline_runs_without_external_credentials() -> None:
    result = CliRunner().invoke(app, ["demo-mail-pipeline"])
    assert result.exit_code == 0
    assert "mail_pipeline pushes=1 application_changes=0" in result.stdout


def test_rebuild_chroma_is_explicitly_unavailable_without_embedding_adapter(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(
        app,
        ["rebuild-chroma"],
        env={
            "APP_DATABASE_PATH": str(tmp_path / "jobs.db"),
            "APP_DATA_DIR": str(tmp_path),
            "APP_BOOTSTRAP_USER_EXTERNAL_KEY": "test-user",
            "APP_BOOTSTRAP_USER_DISPLAY_NAME": "Test User",
            "APP_BOOTSTRAP_MAIL_PROVIDER": "local",
            "APP_BOOTSTRAP_MAIL_ACCOUNT_KEY": "test-account",
            "APP_BOOTSTRAP_MAIL_DISPLAY_NAME": "Test Mail",
            "APP_QQ_USER_OPENID": "openid-1",
            "APP_EMBEDDING_API_KEY": "",
        },
    )

    assert result.exit_code != 0
    assert "rebuild unavailable" in result.stderr
    assert "embedding adapter" in result.stderr


def test_rebuild_chroma_closes_adapters_when_database_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(
        _env_file=None,
        database_path=Path("jobs.db"),
        data_dir=Path("data"),
        bootstrap_user_external_key="test-user",
        bootstrap_user_display_name="Test User",
        bootstrap_mail_provider="local",
        bootstrap_mail_account_key="test-account",
        bootstrap_mail_display_name="Test Mail",
        qq_user_openid="openid-1",
    )
    close_calls = 0
    dispose_calls = 0

    class FakeResources:
        embedding = object()
        chroma = object()

        def close(self) -> None:
            nonlocal close_calls
            close_calls += 1

    class FailingDatabase:
        def __init__(self, path: Path) -> None:
            message = "database construction failed"
            raise RuntimeError(message)

        def dispose(self) -> None:
            nonlocal dispose_calls
            dispose_calls += 1

    monkeypatch.setattr(cli_operations, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_operations, "adapter_factory", lambda _: FakeResources())
    monkeypatch.setattr(cli_operations, "Database", FailingDatabase)

    result = CliRunner().invoke(app, ["rebuild-chroma"])

    assert result.exit_code != 0
    assert close_calls == 1
    assert dispose_calls == 0
