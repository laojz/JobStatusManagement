import json
from pathlib import Path
from uuid import UUID

import pytest
from botpy.protocol.transport import WebhookRequest, WebhookResponse
from pydantic import SecretStr
from sqlalchemy import text
from starlette.testclient import TestClient

from jobs_status_manager.agent.botpy_ingestion import receive_botpy_event
from jobs_status_manager.agent.webhook import WebhookContext
from jobs_status_manager.application.lifecycle import LifecycleAdapters, create_app
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import FakeQQGateway
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.infrastructure.qq_botpy import (
    DurableEventHandler,
    LifespanBotpyRuntime,
)


def _setup(database: Database, settings: AppSettings, clock: FakeClock) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{database.engine.url.database}")
    identifiers = [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(1, 10)]
    bootstrap_identity(database, settings, clock, DeterministicIdGenerator(identifiers))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [("attachments", [123]), ("files", {"id": "one"})],
)
async def test_botpy_malformed_single_attachment_is_rejected_before_persistence(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    field: str,
    value: list[int] | dict[str, str],
) -> None:
    _setup(database, settings, fake_clock)
    context = WebhookContext(
        database,
        settings,
        fake_clock,
        DeterministicIdGenerator([UUID("00000000-0000-0000-0000-000000000010")]),
    )
    payload = {
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "event_id": "malformed-event",
            "message_id": "malformed-message",
            "author": {"user_openid": settings.qq_user_openid},
            field: value,
        },
    }

    accepted, status_code, body = await receive_botpy_event(json.dumps(payload).encode(), context)

    assert accepted is False
    assert status_code == 400
    assert json.loads(body) == {"error": "invalid event"}


def test_local_webhook_keeps_fake_gateway_token_and_duplicate_behavior(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _setup(database, settings, fake_clock)
    header_value = "local-webhook-token"
    effective = settings.model_copy(
        update={
            "qq_webhook_token": SecretStr(header_value),
            "qq_webhook_path": "/webhooks/custom-qq",
        }
    )
    qq = FakeQQGateway(inbound_token=header_value)
    app = create_app(
        effective,
        Path(__file__).resolve().parents[2],
        LifecycleAdapters(qq=qq, clock=fake_clock),
    )
    payload = {
        "event_id": "characterization-event",
        "message_id": "characterization-message",
        "user_openid": effective.qq_user_openid,
        "msg_seq": 2,
        "content": "local webhook",
    }

    with TestClient(app) as client:
        unauthorized = client.post(effective.qq_webhook_path, json=payload)
        accepted = client.post(
            effective.qq_webhook_path,
            json=payload,
            headers={"x-qq-webhook-token": header_value},
        )
        duplicate = client.post(
            effective.qq_webhook_path,
            json=payload,
            headers={"x-qq-webhook-token": header_value},
        )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 202
    assert duplicate.status_code == 200
    assert [call[0] for call in qq.calls] == [
        "validate_webhook",
        "validate_webhook",
        "receive",
        "validate_webhook",
        "receive",
    ]
    with database.engine.connect() as connection:
        metadata = connection.execute(
            text(
                "SELECT provider_event_id, provider_name, provider_scope, provider_target_id, "
                "provider_message_id, provider_msg_seq FROM conversation_messages"
            )
        ).one()
    assert metadata == (
        "characterization-event",
        "qq",
        "c2c",
        effective.qq_user_openid,
        "characterization-message",
        2,
    )


def test_lifespan_preserves_supplied_fake_gateway_identity(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _setup(database, settings, fake_clock)
    header_value = "local-webhook-token"
    effective = settings.model_copy(update={"qq_webhook_token": SecretStr(header_value)})
    qq = FakeQQGateway(inbound_token=header_value)
    app = create_app(
        effective,
        Path(__file__).resolve().parents[2],
        LifecycleAdapters(qq=qq, clock=fake_clock),
    )

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/qq",
            json={
                "event_id": "ownership-event",
                "message_id": "ownership-message",
                "user_openid": effective.qq_user_openid,
                "content": "fake ownership",
            },
            headers={"x-qq-webhook-token": header_value},
        )

    assert response.status_code == 202
    assert qq.calls[0][0] == "validate_webhook"


def test_no_qq_credentials_keep_runtime_in_no_qq_mode(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup(database, settings, fake_clock)

    def fail_automatic_runtime(*_: object, **__: object) -> LifespanBotpyRuntime:
        pytest.fail("botpy runtime must not be constructed without QQ credentials")

    monkeypatch.setattr(
        "jobs_status_manager.application.lifecycle.create_botpy_runtime",
        fail_automatic_runtime,
    )
    app = create_app(settings, Path(__file__).resolve().parents[2])

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200


def test_explicit_qq_adapter_wins_over_automatic_botpy_with_credentials(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup(database, settings, fake_clock)
    header_value = "local-webhook-token"
    effective = settings.model_copy(
        update={
            "qq_webhook_token": SecretStr(header_value),
            "qq_app_id": "app-id",
            "qq_app_secret": SecretStr("app-secret"),
            "qq_token_base_url": "https://token.example.invalid/",
        }
    )
    qq = FakeQQGateway(inbound_token=header_value)

    def fail_automatic_runtime(*_: object, **__: object) -> LifespanBotpyRuntime:
        pytest.fail("automatic Botpy runtime was constructed despite explicit QQ adapter")

    monkeypatch.setattr(
        "jobs_status_manager.application.lifecycle.create_botpy_runtime",
        fail_automatic_runtime,
    )
    app = create_app(
        effective,
        Path(__file__).resolve().parents[2],
        LifecycleAdapters(qq=qq, clock=fake_clock),
    )

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/qq",
            json={
                "event_id": "explicit-qq-event",
                "message_id": "explicit-qq-message",
                "user_openid": effective.qq_user_openid,
                "content": "explicit fake",
            },
            headers={"x-qq-webhook-token": header_value},
        )

    assert response.status_code == 202
    assert qq.calls[0][0] == "validate_webhook"


def test_disabled_qq_gate_keeps_complete_credentials_in_no_qq_mode(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup(database, settings, fake_clock)
    effective = settings.model_copy(
        update={
            "qq_app_id": "app-id",
            "qq_app_secret": SecretStr("app-secret"),
            "qq_token_base_url": "https://token.example.invalid/",
            "qq_enabled": False,
        }
    )

    def fail_automatic_runtime(*_: object, **__: object) -> LifespanBotpyRuntime:
        pytest.fail("disabled QQ gate must not construct botpy")

    monkeypatch.setattr(
        "jobs_status_manager.application.lifecycle.create_botpy_runtime",
        fail_automatic_runtime,
    )
    app = create_app(effective, Path(__file__).resolve().parents[2])

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200


def test_explicit_qq_adapter_skips_injected_botpy_factory(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _setup(database, settings, fake_clock)
    header_value = "local-webhook-token"
    effective = settings.model_copy(
        update={
            "qq_webhook_token": SecretStr(header_value),
            "qq_app_id": "app-id",
            "qq_app_secret": SecretStr("app-secret"),
            "qq_token_base_url": "https://token.example.invalid/",
        }
    )
    qq = FakeQQGateway(inbound_token=header_value)
    factory_calls: list[str] = []

    def fail_factory(_: AppSettings) -> LifespanBotpyRuntime:
        factory_calls.append("called")
        pytest.fail("injected Botpy factory must not override explicit QQ adapter")

    app = create_app(
        effective,
        Path(__file__).resolve().parents[2],
        LifecycleAdapters(qq=qq, clock=fake_clock, qq_botpy_factory=fail_factory),
    )

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/qq",
            json={
                "event_id": "explicit-factory-event",
                "message_id": "explicit-factory-message",
                "user_openid": effective.qq_user_openid,
                "content": "explicit gateway",
            },
            headers={"x-qq-webhook-token": header_value},
        )

    assert response.status_code == 202
    assert factory_calls == []
    assert qq.calls[0][0] == "validate_webhook"


def test_lifespan_automatically_constructs_botpy_without_explicit_qq(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup(database, settings, fake_clock)
    effective = settings.model_copy(
        update={
            "qq_enabled": True,
            "qq_app_id": "app-id",
            "qq_app_secret": SecretStr("app-secret"),
            "qq_token_base_url": "https://token.example.invalid/",
        }
    )
    lifecycle_calls: list[str] = []

    class FakeClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> None:
            lifecycle_calls.append(f"start:{appid}:{secret}:{ret_coro}")

        async def close(self) -> None:
            lifecycle_calls.append("client-close")

    class FakeTransport:
        async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
            lifecycle_calls.append(f"request:{request.body.decode()}")
            return WebhookResponse(200, b'{"op":12,"d":0}')

        async def wait_ready(self) -> None:
            lifecycle_calls.append("ready")

        async def close(self) -> None:
            lifecycle_calls.append("transport-close")

    def automatic_factory(_: AppSettings, __: DurableEventHandler) -> LifespanBotpyRuntime:
        lifecycle_calls.append("automatic-factory")
        return LifespanBotpyRuntime(FakeClient(), FakeTransport(), "app-id", "app-secret")

    monkeypatch.setattr(
        "jobs_status_manager.application.lifecycle.create_botpy_runtime",
        automatic_factory,
    )
    app = create_app(effective, Path(__file__).resolve().parents[2])

    with TestClient(app) as client:
        response = client.post("/webhooks/qq", content=b"automatic")

    assert response.status_code == 200
    assert lifecycle_calls == [
        "automatic-factory",
        "start:app-id:app-secret:True",
        "ready",
        "request:automatic",
        "client-close",
        "transport-close",
    ]


def test_lifespan_owns_one_explicit_botpy_runtime_and_closes_it(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _setup(database, settings, fake_clock)
    lifecycle_calls: list[str] = []

    class FakeClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> None:
            lifecycle_calls.append(f"start:{appid}:{secret}:{ret_coro}")

        async def close(self) -> None:
            lifecycle_calls.append("client-close")

    class FakeTransport:
        async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
            lifecycle_calls.append(f"request:{request.body.decode()}")
            return WebhookResponse(200, b'{"op":12,"d":0}')

        async def wait_ready(self) -> None:
            return None

        async def close(self) -> None:
            lifecycle_calls.append("transport-close")

    def factory(_: AppSettings) -> LifespanBotpyRuntime:
        lifecycle_calls.append("factory")
        return LifespanBotpyRuntime(FakeClient(), FakeTransport(), "app-id", "app-secret")

    app = create_app(
        settings,
        Path(__file__).resolve().parents[2],
        LifecycleAdapters(qq_botpy_factory=factory),
    )

    with TestClient(app) as client:
        response = client.post("/webhooks/qq", content=b'{"op":0}')
        assert response.status_code == 200

    assert lifecycle_calls == [
        "factory",
        "start:app-id:app-secret:True",
        'request:{"op":0}',
        "client-close",
        "transport-close",
    ]
