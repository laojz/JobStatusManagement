"""Phase 1 local CLI demo end-to-end test."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobs_status_manager.bootstrap.cli import app


def test_demo_status_core_runs_through_real_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The demo uses proposal/router/service code and reports completion."""
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "data" / "jobs.db"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_BOOTSTRAP_USER_EXTERNAL_KEY", "demo-user")
    monkeypatch.setenv("APP_BOOTSTRAP_USER_DISPLAY_NAME", "Demo User")
    monkeypatch.setenv("APP_BOOTSTRAP_MAIL_PROVIDER", "local")
    monkeypatch.setenv("APP_BOOTSTRAP_MAIL_ACCOUNT_KEY", "demo-account")
    monkeypatch.setenv("APP_BOOTSTRAP_MAIL_DISPLAY_NAME", "Demo Mail")
    monkeypatch.setenv("APP_QQ_USER_OPENID", "openid-demo")
    result = CliRunner().invoke(app, ["demo-status-core"])
    assert result.exit_code == 0
    assert "execution=COMPLETED" in result.stdout
