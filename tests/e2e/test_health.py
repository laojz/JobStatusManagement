"""Foundation HTTP lifecycle tests."""

from pathlib import Path
from typing import Never

import pytest
from starlette.testclient import TestClient

from jobs_status_manager.agent.contracts import ConversationPrompt, ConversationResponse
from jobs_status_manager.application.lifecycle import LifecycleAdapters, create_app
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import FakeIMAPGateway, FakeLLM
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
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
            "schema_version": "0008_qq_reply_targets",
            "readiness": "ready",
            "durable_tasks": "ok",
            "failed_tasks": 0,
            "stale_tasks": 0,
            "runtime_mode": "local",
            "imap": "disabled",
            "llm": "disabled",
            "product_readiness": "local_only",
        }


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
