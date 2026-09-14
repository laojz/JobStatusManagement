"""Foundation HTTP lifecycle tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Never

import pytest
from alembic import command
from sqlalchemy import text
from starlette.testclient import TestClient

from jobs_status_manager.agent.contracts import ConversationPrompt, ConversationResponse
from jobs_status_manager.application.lifecycle import LifecycleAdapters, create_app
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import FakeIMAPGateway, FakeLLM
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import (
    migration_config,
    upgrade_database,
)
from jobs_status_manager.mail import JobMailAnalysisInput, MailPollBatch


def test_health_startup_and_shutdown(settings: AppSettings) -> None:
    """Health reports ready only after the schema exists and lifecycle closes cleanly."""
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    app = create_app(settings, root)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "phase": "6",
            "database": "ok",
            "schema_version": "0009_tool_call_provider_metadata",
            "readiness": "ready",
            "durable_tasks": "ok",
            "failed_tasks": 0,
            "stale_tasks": 0,
            "runtime_mode": "local",
            "imap": "disabled",
            "llm": "disabled",
            "product_readiness": "local_only",
        }


def test_live_is_successful_without_database_or_external_capabilities(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(
        "jobs_status_manager.application.lifecycle._startup_recovery",
        lambda database, adapters, runtime_settings: None,
    )
    app = create_app(settings, root)

    with TestClient(app) as client:
        response = client.get("/live")
        health_response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert health_response.status_code == 503
    assert health_response.json()["database"] == "not_ready"


def test_health_rejects_schema_that_is_not_at_head(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database_url = f"sqlite:///{settings.database_path}"
    upgrade_database(root, database_url)
    command.downgrade(migration_config(root, database_url), "0007_phase6_reliability")
    monkeypatch.setattr(
        "jobs_status_manager.application.lifecycle._startup_recovery",
        lambda database, adapters, runtime_settings: None,
    )
    app = create_app(settings, root)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["database"] == "not_ready"
    assert response.json()["schema_version"] == "0007_phase6_reliability"
    assert response.json()["readiness"] == "not_ready"


def test_health_reports_failed_and_stale_tasks_without_process_failure(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    now = datetime.now(UTC).replace(tzinfo=None)
    now_text = now.isoformat(sep=" ")
    stale_text = (now - timedelta(days=1)).isoformat(sep=" ")
    database = Database(settings.database_path)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, external_key, display_name, created_at, updated_at) "
                    "VALUES ('user', 'external', 'User', :now, :now)"
                ),
                {"now": now_text},
            )
            connection.execute(
                text(
                    "INSERT INTO notifications "
                    "(id, user_id, type, channel, title, content, source_event_id, state, "
                    "attempt_count, last_attempt_at, last_error, created_at, updated_at) "
                    "VALUES (:failed_id, 'user', 'TEST', 'local', 'failed', 'redacted', "
                    "'event-failed', 'FAILED', 1, :now, 'bounded failure', :now, :now), "
                    "(:stale_id, 'user', 'TEST', 'local', 'stale', 'redacted', "
                    "'event-stale', 'SENDING', 1, :stale_at, NULL, :now, :now)"
                ),
                {
                    "failed_id": "failed-notification",
                    "stale_id": "stale-notification",
                    "now": now_text,
                    "stale_at": stale_text,
                },
            )
    finally:
        database.dispose()
    monkeypatch.setattr(
        "jobs_status_manager.application.lifecycle._startup_recovery",
        lambda database, adapters, runtime_settings: None,
    )
    monkeypatch.setattr(
        "jobs_status_manager.application.lifecycle._recover_notifications",
        lambda database, adapters: None,
    )
    app = create_app(settings, root)

    with TestClient(app) as client:
        response = client.get("/health")

    payload = response.json()
    assert response.status_code == 200
    assert payload["durable_tasks"] == "degraded"
    assert payload["failed_tasks"] == 1
    assert payload["stale_tasks"] == 1


def test_health_reports_database_unavailable_while_live_stays_successful(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(
        "jobs_status_manager.application.lifecycle._startup_recovery",
        lambda database, adapters, runtime_settings: None,
    )
    monkeypatch.setattr(
        "jobs_status_manager.application.health.current_revision",
        lambda database_url: _raise_database_failure(),
    )
    app = create_app(settings, root)

    with TestClient(app) as client:
        live_response = client.get("/live")
        health_response = client.get("/health")

    assert live_response.status_code == 200
    assert health_response.status_code == 503
    assert health_response.json()["database"] == "unavailable"
    assert health_response.json()["schema_version"] is None
    assert health_response.json()["readiness"] == "not_ready"


def _raise_database_failure() -> Never:
    raise RuntimeError


def test_production_health_rejects_explicit_fake_external_adapters(
    settings: AppSettings,
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    production = settings.model_copy(
        update={
            "runtime_mode": "production",
            "imap_enabled": True,
            "imap_account": "imap@example.com",
            "imap_auth_code": "imap-secret",
            "llm_enabled": True,
            "llm_base_url": "https://llm.example.invalid/v1",
            "llm_api_key": "llm-secret",
        }
    )
    app = create_app(
        production,
        root,
        adapters=LifecycleAdapters(
            imap=FakeIMAPGateway(),
            llm=FakeLLM(),
        ),
    )
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["product_readiness"] == "not_ready"
        assert response.json()["imap"] == "fake"
        assert response.json()["llm"] == "fake"


def test_production_health_reports_ready_for_constructed_capabilities(
    settings: AppSettings,
) -> None:
    class ReadyIMAP:
        def poll(self, account_key: str, cursor: str | None) -> MailPollBatch:
            raise AssertionError

    class ReadyLLM:
        def analyze_job_mail(self, prompt: str) -> JobMailAnalysisInput:
            raise AssertionError

        def converse(self, prompt: ConversationPrompt) -> ConversationResponse:
            raise AssertionError

    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    production = settings.model_copy(
        update={
            "runtime_mode": "production",
            "imap_enabled": True,
            "imap_account": "imap@example.com",
            "imap_auth_code": "imap-secret",
            "llm_enabled": True,
            "llm_base_url": "https://llm.example.invalid/v1",
            "llm_api_key": "llm-secret",
        }
    )
    app = create_app(
        production,
        root,
        adapters=LifecycleAdapters(imap=ReadyIMAP(), llm=ReadyLLM()),
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["product_readiness"] == "ready"
    assert response.json()["imap"] == "ready"
    assert response.json()["llm"] == "ready"


def test_local_health_reports_not_ready_when_adapter_construction_fails(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    local = settings.model_copy(
        update={
            "imap_enabled": True,
            "imap_account": "imap@example.com",
            "imap_auth_code": "imap-secret",
            "llm_enabled": True,
            "llm_base_url": "https://llm.example.invalid/v1",
            "llm_api_key": "llm-secret",
        }
    )

    def fail_llm(*args: str, **kwargs: str) -> Never:
        message = "llm construction failed"
        raise RuntimeError(message)

    monkeypatch.setattr(
        "jobs_status_manager.infrastructure.adapters.factory.OpenAICompatibleLLM",
        fail_llm,
    )
    app = create_app(local, root)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["imap"] == "not_ready"
    assert response.json()["llm"] == "not_ready"
    assert response.json()["product_readiness"] == "not_ready"
