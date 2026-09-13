import anyio
import pytest

from jobs_status_manager.agent.contracts import ConversationResponse
from jobs_status_manager.application.lifecycle import (
    LifecycleAdapters,
    _cleanup_lifecycle_resources,
    _readiness_state,
)
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import FakeIMAPGateway, FakeLLM


def test_readiness_marks_local_disabled_capabilities_local_only(settings: AppSettings) -> None:
    state = _readiness_state(settings, LifecycleAdapters())

    assert state["runtime_mode"] == "local"
    assert state["imap"] == "disabled"
    assert state["llm"] == "disabled"
    assert state["product_readiness"] == "local_only"


def test_readiness_rejects_fakes_in_production(settings: AppSettings) -> None:
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
    adapters = LifecycleAdapters(
        imap=FakeIMAPGateway(),
        llm=FakeLLM(conversation_responses=[ConversationResponse(answer="answer")]),
    )

    state = _readiness_state(production, adapters)

    assert state["imap"] == "fake"
    assert state["llm"] == "fake"
    assert state["product_readiness"] == "not_ready"


def test_cleanup_releases_qq_before_owned_adapters_and_database() -> None:
    events: list[str] = []

    class QQRuntime:
        async def close(self) -> None:
            events.append("qq")

    class Owned:
        def close(self) -> None:
            events.append("owned")

    class Database:
        def dispose(self) -> None:
            events.append("database")

    anyio.run(_cleanup_lifecycle_resources, QQRuntime(), Owned(), Database())

    assert events == ["qq", "owned", "database"]


def test_cleanup_preserves_first_error_and_attempts_remaining_resources() -> None:
    events: list[str] = []

    class QQRuntime:
        async def close(self) -> None:
            events.append("qq")

    class Owned:
        def close(self) -> None:
            events.append("owned")
            message = "owned close failed"
            raise RuntimeError(message)

    class Database:
        def dispose(self) -> None:
            events.append("database")
            message = "database dispose failed"
            raise ValueError(message)

    with pytest.raises(RuntimeError, match="owned close failed"):
        anyio.run(_cleanup_lifecycle_resources, QQRuntime(), Owned(), Database())

    assert events == ["qq", "owned", "database"]


def test_cleanup_attempts_remaining_resources_after_unexpected_qq_error() -> None:
    # Given
    events: list[str] = []

    class QQRuntime:
        async def close(self) -> None:
            events.append("qq")
            message = "qq cleanup failed"
            raise AttributeError(message)

    class Owned:
        def close(self) -> None:
            events.append("owned")

    class Database:
        def dispose(self) -> None:
            events.append("database")

    # When
    with pytest.raises(AttributeError, match="qq cleanup failed"):
        anyio.run(_cleanup_lifecycle_resources, QQRuntime(), Owned(), Database())

    # Then
    assert events == ["qq", "owned", "database"]
