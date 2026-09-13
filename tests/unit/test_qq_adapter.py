import json
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Self
from unittest.mock import Mock
from uuid import UUID

import anyio
import botpy
import pytest
from botpy.protocol.message import MediaSendResult, MessageType
from botpy.protocol.models import RawEvent
from botpy.protocol.models import ReplyTarget as SdkReplyTarget
from botpy.protocol.transport import (
    WebhookRequest,
    WebhookResponse,
    ed25519_sign,
    sign_validation_response,
)
from pydantic import SecretStr

from jobs_status_manager.agent import uploads
from jobs_status_manager.agent.botpy_ingestion import receive_botpy_event
from jobs_status_manager.agent.contracts import ProviderErrorKind, ReplyMode, ReplyTarget
from jobs_status_manager.agent.uploads import validate_attachment_url
from jobs_status_manager.agent.webhook import WebhookContext
from jobs_status_manager.application.lifecycle import _cleanup_lifecycle_resources
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.infrastructure.qq_botpy import (
    BotpyQQGateway,
    BotpySdkResult,
    DurableIngestionReceipt,
    LifespanBotpyRuntime,
    StarletteEventTransport,
    _api_error_result,
    create_botpy_runtime,
)
from jobs_status_manager.infrastructure.qq_botpy_events import normalize_sdk_event

ROOT_BOTPY_LOG = Path(__file__).resolve().parents[2] / "botpy.log"
BOTPY_FACTORY_SUBPROCESS = (
    "import os\n"
    "import sys\n"
    "from pathlib import Path\n"
    "from pydantic import SecretStr\n"
    "from jobs_status_manager.config.settings import AppSettings\n"
    "from jobs_status_manager.infrastructure.qq_botpy import create_botpy_runtime\n"
    "async def persist(*args):\n"
    "    return None\n"
    "os.chdir(sys.argv[1])\n"
    "settings = AppSettings(\n"
    "    _env_file=None,\n"
    "    database_path=Path('database.sqlite3'),\n"
    "    data_dir=Path('data'),\n"
    "    bootstrap_user_external_key='user-key',\n"
    "    bootstrap_user_display_name='User',\n"
    "    bootstrap_mail_provider='local',\n"
    "    bootstrap_mail_account_key='account-key',\n"
    "    bootstrap_mail_display_name='Mailbox',\n"
    "    qq_user_openid='openid',\n"
    "    qq_app_id='app-id',\n"
    "    qq_app_secret=SecretStr('secret'),\n"
    "    qq_token_base_url='https://token.example.invalid/',\n"
    ")\n"
    "create_botpy_runtime(settings, persist)\n"
)


def _signed_request(
    body: bytes,
    credential: str,
    timestamp: str | None = None,
) -> WebhookRequest:
    effective_timestamp = timestamp or str(int(time.time()))
    return WebhookRequest(
        body=body,
        headers={
            "X-Signature-Timestamp": effective_timestamp,
            "X-Signature-Ed25519": ed25519_sign(
                credential,
                effective_timestamp.encode() + body,
            ),
        },
    )


def _run_operation(operation: Awaitable[BotpySdkResult]) -> BotpySdkResult:
    async def run() -> BotpySdkResult:
        return await operation

    return anyio.run(run)


def test_gateway_maps_passive_target_to_botpy_send() -> None:
    calls: list[
        tuple[SdkReplyTarget, str | None, MessageType | None, Mapping[str, object] | None]
    ] = []
    client = Mock()

    async def send(
        target: SdkReplyTarget,
        *,
        content: str | None = None,
        msg_type: MessageType | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> MediaSendResult:
        calls.append((target, content, msg_type, extra))
        return MediaSendResult(upload={}, message={"id": "sent-message"})

    client.send.side_effect = send

    target = ReplyTarget(
        mode=ReplyMode.PASSIVE,
        provider_name="qq",
        provider_scope="c2c",
        target_id="openid",
        message_id="message-id",
        event_id="event-id",
        msg_seq=7,
    )
    result = BotpyQQGateway(client, "configured-openid", 1, _run_operation).deliver(
        target,
        "hello",
    )

    assert result.success
    assert result.provider_message_id == "sent-message"
    sdk_target, content, msg_type, extra = calls[0]
    assert sdk_target.message_id == "message-id"
    assert sdk_target.event_id == "event-id"
    assert sdk_target.target_id == "openid"
    assert content == "hello"
    assert msg_type is MessageType.TEXT
    assert extra == {"msg_seq": 7}


def test_gateway_legacy_reply_rejects_missing_event_id_without_proactive_fallback() -> None:
    client = Mock()
    gateway = BotpyQQGateway(client, "configured-openid", 1, _run_operation)

    result = gateway.reply("inbound-openid", "message-id", "hello")

    assert result.success is False
    assert result.provider_error is not None
    assert result.provider_error.kind is ProviderErrorKind.PERMANENT
    assert result.provider_error.code == "passive_target_requires_event_id"
    client.send.assert_not_called()


def test_gateway_maps_media_operations_to_botpy_media_methods() -> None:
    operations: list[str] = []
    client = Mock()

    async def send_image(target: SdkReplyTarget, *, data: bytes) -> MediaSendResult:
        operations.append(f"image:{target.target_id}:{data.decode()}")
        return MediaSendResult(upload={}, message={"id": "image-message"})

    async def send_file(
        target: SdkReplyTarget,
        *,
        data: bytes,
        file_name: str,
    ) -> MediaSendResult:
        operations.append(f"file:{target.target_id}:{file_name}:{data.decode()}")
        return MediaSendResult(upload={}, message={"id": "file-message"})

    client.send_image.side_effect = send_image
    client.send_file.side_effect = send_file
    gateway = BotpyQQGateway(client, "configured-openid", 1, _run_operation)
    target = ReplyTarget(
        mode=ReplyMode.PROACTIVE,
        provider_name="qq",
        provider_scope="c2c",
        target_id="openid",
    )

    image = gateway.send_image(target, "image/png", b"image")
    file = gateway.send_file(target, "notes.txt", "text/plain", b"file")

    assert image.provider_message_id == "image-message"
    assert file.provider_message_id == "file-message"
    assert operations == ["image:openid:image", "file:openid:notes.txt:file"]


async def _noop_event(_: bytes, __: Mapping[str, str]) -> DurableIngestionReceipt:
    return DurableIngestionReceipt.accepted_receipt()


@pytest.mark.anyio
async def test_factory_constructs_one_sdk_client_and_custom_transport(
    settings: AppSettings,
    tmp_path: Path,
) -> None:
    assert not await anyio.to_thread.run_sync(ROOT_BOTPY_LOG.exists)
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        effective = settings.model_copy(
            update={
                "qq_app_id": "test-app-id",
                "qq_app_secret": SecretStr("test-app-secret"),
                "qq_token_base_url": "https://token.example.invalid/",
                "qq_dispatch_timestamp_max_age_seconds": 45,
            }
        )

        runtime = create_botpy_runtime(effective, _noop_event)
        await runtime.close()
    finally:
        os.chdir(original_cwd)

    assert isinstance(runtime.client, botpy.Client)
    assert isinstance(runtime.transport, StarletteEventTransport)
    assert runtime.transport._dispatch_timestamp_max_age_seconds == 45
    assert not (tmp_path / "botpy.log").exists()
    assert not await anyio.to_thread.run_sync(ROOT_BOTPY_LOG.exists)


@pytest.mark.anyio
async def test_factory_passes_both_botpy_logging_controls(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CapturingClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> None:
            return None

        async def close(self) -> None:
            return None

    client_factory = Mock(return_value=CapturingClient())
    monkeypatch.setattr(botpy, "Client", client_factory)
    effective = settings.model_copy(
        update={
            "qq_app_id": "test-app-id",
            "qq_app_secret": SecretStr("test-app-secret"),
            "qq_token_base_url": "https://token.example.invalid/",
        }
    )

    runtime = create_botpy_runtime(effective, _noop_event)
    await runtime.close()

    assert client_factory.call_count == 1
    assert client_factory.call_args is not None
    assert client_factory.call_args.kwargs["bot_log"] is False
    assert client_factory.call_args.kwargs["ext_handlers"] is False


def test_factory_does_not_create_botpy_logs_after_interpreter_exit(tmp_path: Path) -> None:
    temporary_cwd = tmp_path / "subprocess-cwd"
    temporary_cwd.mkdir()
    temporary_log = temporary_cwd / "botpy.log"
    paths = (ROOT_BOTPY_LOG, temporary_log)
    existed_before = {path: path.exists() for path in paths}
    assert not any(existed_before.values())
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT_BOTPY_LOG.parent)

    try:
        for child_cwd in (ROOT_BOTPY_LOG.parent, temporary_cwd):
            result = subprocess.run(  # noqa: S603
                [sys.executable, "-c", BOTPY_FACTORY_SUBPROCESS, str(child_cwd)],
                capture_output=True,
                cwd=ROOT_BOTPY_LOG.parent,
                env=environment,
                text=True,
                check=False,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert not ROOT_BOTPY_LOG.exists()
            assert not temporary_log.exists()
    finally:
        for path, existed in existed_before.items():
            if not existed and path.exists():
                path.unlink()


@pytest.mark.anyio
@pytest.mark.parametrize("opcode", [True, False], ids=["true", "false"])
async def test_boolean_opcode_is_rejected_without_persistence(opcode: bool) -> None:
    calls: list[bytes] = []

    async def persist(body: bytes, _: Mapping[str, str]) -> DurableIngestionReceipt:
        calls.append(body)
        return DurableIngestionReceipt.accepted_receipt()

    transport = StarletteEventTransport("app-id", "test-app-secret", persist)
    body = json.dumps({"op": opcode, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()

    response = await transport.handle_request(WebhookRequest(body=body, headers={}))

    assert response.status == 400
    assert json.loads(response.body) == {"error": "invalid payload"}
    assert calls == []


def test_op13_matches_sdk_validation_response() -> None:
    credential = "test-app-secret"
    payload = {"op": 13, "d": {"plain_token": "plain", "event_ts": "1700000000"}}
    transport = StarletteEventTransport("app-id", credential, _noop_event)
    assert not hasattr(transport, "listen")

    response = anyio.run(
        transport.handle_request,
        WebhookRequest(body=json.dumps(payload).encode(), headers={}),
    )

    assert response.status == 200
    validation_input = {"plain_token": "plain", "event_ts": "1700000000", "bot_secret": credential}
    assert json.loads(response.body) == sign_validation_response(**validation_input)


@pytest.mark.anyio
async def test_validation_signature_cannot_be_reused_for_dispatch() -> None:
    calls: list[bytes] = []
    event_body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    timestamp = str(int(time.time()))

    async def persist(body: bytes, _: Mapping[str, str]) -> DurableIngestionReceipt:
        calls.append(body)
        return DurableIngestionReceipt.accepted_receipt()

    transport = StarletteEventTransport("app-id", "test-app-secret", persist)
    validation_payload = {
        "op": 13,
        "d": {"plain_token": event_body.decode(), "event_ts": timestamp},
    }
    validation_response = await transport.handle_request(
        WebhookRequest(body=json.dumps(validation_payload).encode(), headers={})
    )
    forged_signature = json.loads(validation_response.body)["signature"]

    dispatch_response = await transport.handle_request(
        WebhookRequest(
            body=event_body,
            headers={
                "X-Signature-Timestamp": timestamp,
                "X-Signature-Ed25519": forged_signature,
            },
        )
    )

    assert validation_response.status == 200
    assert dispatch_response.status == 401
    assert calls == []


@pytest.mark.anyio
async def test_repeated_and_unique_validation_challenges_remain_sdk_compatible() -> None:
    transport = StarletteEventTransport("app-id", "test-app-secret", _noop_event)
    repeated_payload = {
        "op": 13,
        "d": {"plain_token": "repeat", "event_ts": "1700000000"},
    }
    repeated_validation_input = {
        "plain_token": "repeat",
        "event_ts": "1700000000",
        "bot_secret": "test-app-secret",
    }
    expected_repeated = sign_validation_response(**repeated_validation_input)

    for _ in range(2):
        response = await transport.handle_request(
            WebhookRequest(body=json.dumps(repeated_payload).encode(), headers={})
        )
        assert response.status == 200
        assert json.loads(response.body) == expected_repeated

    for index in range(1100):
        payload = {
            "op": 13,
            "d": {"plain_token": f"plain-{index}", "event_ts": str(index)},
        }
        response = await transport.handle_request(
            WebhookRequest(body=json.dumps(payload).encode(), headers={})
        )
        assert response.status == 200
        validation_input = {
            "plain_token": f"plain-{index}",
            "event_ts": str(index),
            "bot_secret": "test-app-secret",
        }
        assert json.loads(response.body) == sign_validation_response(**validation_input)


@pytest.mark.anyio
async def test_validation_signature_replay_stays_rejected_after_challenge_stress() -> None:
    calls: list[bytes] = []
    event_body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    timestamp = str(int(time.time()) - 301)

    async def persist(body: bytes, _: Mapping[str, str]) -> DurableIngestionReceipt:
        calls.append(body)
        return DurableIngestionReceipt.accepted_receipt()

    transport = StarletteEventTransport("app-id", "test-app-secret", persist)
    first_response = await transport.handle_request(
        WebhookRequest(
            body=json.dumps(
                {
                    "op": 13,
                    "d": {"plain_token": event_body.decode(), "event_ts": timestamp},
                }
            ).encode(),
            headers={},
        )
    )
    first_signature = json.loads(first_response.body)["signature"]

    for index in range(1024):
        response = await transport.handle_request(
            WebhookRequest(
                body=json.dumps(
                    {
                        "op": 13,
                        "d": {"plain_token": f"plain-{index}", "event_ts": timestamp},
                    }
                ).encode(),
                headers={},
            )
        )
        assert response.status == 200

    replay_response = await transport.handle_request(
        WebhookRequest(
            body=event_body,
            headers={
                "X-Signature-Timestamp": timestamp,
                "X-Signature-Ed25519": first_signature,
            },
        )
    )

    assert first_response.status == 200
    assert replay_response.status == 401
    assert calls == []


@pytest.mark.anyio
async def test_valid_dispatch_signature_is_accepted() -> None:
    calls: list[bytes] = []

    async def persist(body: bytes, _: Mapping[str, str]) -> DurableIngestionReceipt:
        calls.append(body)
        return DurableIngestionReceipt.accepted_receipt()

    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    transport = StarletteEventTransport("app-id", "test-app-secret", persist)

    response = await transport.handle_request(_signed_request(body, "test-app-secret"))

    assert response.status == 200
    assert calls == [body]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("timestamp_offset", "expected_status"),
    [(-301, 401), (0, 200), (301, 401)],
)
async def test_dispatch_timestamp_must_be_fresh_before_persistence(
    timestamp_offset: int,
    expected_status: int,
) -> None:
    calls: list[bytes] = []
    now = 1_700_000_000
    clock = FakeClock(datetime.fromtimestamp(now, UTC))

    async def persist(body: bytes, _: Mapping[str, str]) -> DurableIngestionReceipt:
        calls.append(body)
        return DurableIngestionReceipt.accepted_receipt()

    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    transport = StarletteEventTransport(
        "app-id",
        "test-app-secret",
        persist,
        dispatch_timestamp_max_age_seconds=300,
        clock=clock,
    )

    response = await transport.handle_request(
        _signed_request(
            body,
            "test-app-secret",
            timestamp=str(now + timestamp_offset),
        )
    )

    assert response.status == expected_status
    assert calls == ([body] if expected_status == 200 else [])


def test_sdk_c2c_file_event_normalizes_to_provider_neutral_contract() -> None:
    payload = {
        "op": 0,
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "event_id": "sdk-event",
            "message_id": "sdk-message",
            "msg_seq": 4,
            "author": {"user_openid": "sdk-openid"},
            "content": "附件",
            "attachments": [
                {
                    "id": "sdk-file",
                    "filename": "notes.txt",
                    "content_type": "text/plain",
                    "size": 12,
                    "url": "https://files.example.invalid/notes.txt",
                }
            ],
        },
    }

    event = normalize_sdk_event(payload)

    assert event is not None
    assert event.event_type == "C2C_FILE_CREATE"
    assert event.event_id == "sdk-event"
    assert event.message_id == "sdk-message"
    assert event.user_openid == "sdk-openid"
    assert event.file_url == "https://files.example.invalid/notes.txt"


def test_sdk_multiple_attachments_are_rejected() -> None:
    payload = {
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "event_id": "event",
            "message_id": "message",
            "author": {"user_openid": "openid"},
            "attachments": [
                {"id": "one", "filename": "one.txt", "content_type": "text/plain", "size": 1},
                {"id": "two", "filename": "two.txt", "content_type": "text/plain", "size": 1},
            ],
        },
    }

    assert normalize_sdk_event(payload) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attachments", [123]),
        ("attachments", [None]),
        ("attachments", "not-a-list"),
        ("attachments", {"id": "one"}),
        ("files", [123]),
        ("files", [None]),
        ("files", "not-a-list"),
        ("files", {"id": "one"}),
    ],
)
def test_malformed_single_attachment_is_rejected(
    field: str, value: list[int] | list[None] | str | dict[str, str]
) -> None:
    payload = {
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "event_id": "event",
            "message_id": "message",
            "author": {"user_openid": "openid"},
            field: value,
        },
    }

    assert normalize_sdk_event(payload) is None


@pytest.mark.parametrize("field", [None, "attachments", "files"])
def test_absent_or_empty_attachment_fields_still_normalize_as_text(
    field: str | None,
) -> None:
    data: dict[str, object] = {
        "event_id": "event",
        "message_id": "message",
        "author": {"user_openid": "openid"},
    }
    if field is not None:
        data[field] = []

    event = normalize_sdk_event({"t": "C2C_MESSAGE_CREATE", "d": data})

    assert event is not None
    assert event.event_type == "C2C_MESSAGE_CREATE"
    assert event.content == "消息"


@pytest.mark.parametrize(
    ("field", "entries"),
    [
        ("attachments", [{"id": "one"}, "not-a-mapping"]),
        ("files", [1, {"id": "one"}]),
        ("attachments", [1, 2]),
    ],
)
def test_mixed_multiple_attachments_are_rejected_by_normalization(
    field: str, entries: list[object]
) -> None:
    payload = {
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "event_id": "event",
            "message_id": "message",
            "author": {"user_openid": "openid"},
            field: entries,
        },
    }

    assert normalize_sdk_event(payload) is None


@pytest.mark.anyio
async def test_mixed_multiple_attachments_are_rejected_before_persistence(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    ids: DeterministicIdGenerator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = WebhookContext(database, settings, fake_clock, ids)
    payload = {
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "event_id": "event",
            "message_id": "message",
            "author": {"user_openid": settings.qq_user_openid},
            "attachments": [{"id": "one"}, "not-a-mapping"],
        },
    }

    def fail_if_persisted(*_: object, **__: object) -> None:
        raise AssertionError

    monkeypatch.setattr(
        "jobs_status_manager.agent.botpy_ingestion._persist_event",
        fail_if_persisted,
    )
    accepted, status_code, _ = await receive_botpy_event(json.dumps(payload).encode(), context)

    assert accepted is False
    assert status_code == 422


@pytest.mark.anyio
async def test_botpy_multiple_attachments_are_rejected_without_persistence(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    ids: DeterministicIdGenerator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = WebhookContext(database, settings, fake_clock, ids)
    payload = {
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "event_id": "event",
            "message_id": "message",
            "author": {"user_openid": settings.qq_user_openid},
            "attachments": [
                {"id": "one", "filename": "one.txt", "content_type": "text/plain", "size": 1},
                {"id": "two", "filename": "two.txt", "content_type": "text/plain", "size": 1},
            ],
        },
    }

    def fail_if_persisted(*_: object, **__: object) -> None:
        raise AssertionError

    monkeypatch.setattr(
        "jobs_status_manager.agent.botpy_ingestion._persist_event",
        fail_if_persisted,
    )
    accepted, status_code, _ = await receive_botpy_event(json.dumps(payload).encode(), context)

    assert accepted is False
    assert status_code == 422


@pytest.mark.anyio
async def test_botpy_valid_unsupported_event_is_ignored_without_persistence(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    ids: DeterministicIdGenerator,
) -> None:
    context = WebhookContext(database, settings, fake_clock, ids)

    accepted, status_code, body = await receive_botpy_event(
        json.dumps({"t": "READY", "d": {"session_id": "session"}}).encode(),
        context,
    )

    assert accepted is True
    assert status_code == 202
    assert body == b'{"status":"ignored"}'


@pytest.mark.anyio
async def test_botpy_arbitrary_well_formed_unsupported_event_is_ignored(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    ids: DeterministicIdGenerator,
) -> None:
    context = WebhookContext(database, settings, fake_clock, ids)

    accepted, status_code, body = await receive_botpy_event(
        json.dumps({"t": "MESSAGE_DELETE", "d": {"id": "event"}}).encode(),
        context,
    )

    assert accepted is True
    assert status_code == 202
    assert body == b'{"status":"ignored"}'


@pytest.mark.anyio
async def test_botpy_malformed_supported_event_is_not_successful(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    ids: DeterministicIdGenerator,
) -> None:
    context = WebhookContext(database, settings, fake_clock, ids)

    accepted, status_code, _ = await receive_botpy_event(
        json.dumps({"t": "C2C_MESSAGE_CREATE", "d": {}}).encode(),
        context,
    )

    assert accepted is False
    assert status_code == 400


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1/file.txt", "https required"),
        ("https://127.0.0.1/file.txt", "private address"),
        ("https://[::1]/file.txt", "private address"),
    ],
)
def test_attachment_url_rejects_unsafe_destinations(url: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        validate_attachment_url(url)


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_qq_http_delivery_statuses_are_retryable(status: int) -> None:
    result = _api_error_result(Mock(status=status))

    assert result.success is False
    assert result.provider_error is not None
    assert result.provider_error.kind.value == "RETRYABLE"


def test_attachment_url_rejects_cgnat_address(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(
        *_: object,
        **__: object,
    ) -> list[tuple[object, object, object, object, tuple[str, int]]]:
        return [(0, 0, 0, "", ("100.64.0.1", 0))]

    monkeypatch.setattr("jobs_status_manager.agent.uploads.socket.getaddrinfo", resolve)

    with pytest.raises(ValueError, match="private address"):
        validate_attachment_url("https://shared.example/file.txt")


def test_attachment_url_rejects_malformed_url_without_leaking_value_error() -> None:
    with pytest.raises(ValueError, match="attachment URL is invalid"):
        validate_attachment_url("https://[broken/file.txt")


def test_url_download_stream_uses_smaller_qq_limit(tmp_path: Path) -> None:
    class Response:
        status_code = 200

        def __init__(self) -> None:
            self.headers = {"content-type": "text/plain"}

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def iter_bytes(self, _: int) -> tuple[bytes]:
            return (b"0123456789",)

    class Client(uploads.httpx2.Client):
        def stream(self, *_: object, **__: object) -> Response:
            return Response()

    metadata = uploads.UploadMetadata("file", "notes.txt", "text/plain", 1)
    request = uploads._DownloadRequest(
        AppSettings.model_construct(
            max_upload_bytes=100,
            qq_max_download_bytes=5,
            qq_download_timeout_seconds=1,
        ),
        Client(),
        "https://example.test/notes.txt",
        tmp_path / "qq-download-test.part",
        metadata,
        time.monotonic() + 10,
    )

    with pytest.raises(ValueError, match="configured limit"):
        uploads._fetch_attachment(request)
    assert not request.temporary_path.exists()


def test_url_download_requires_declared_response_content_type(tmp_path: Path) -> None:
    class Response:
        status_code = 200

        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def iter_bytes(self, _: int) -> tuple[bytes]:
            return (b"notes",)

    class Client(uploads.httpx2.Client):
        def stream(self, *_: object, **__: object) -> Response:
            return Response()

    metadata = uploads.UploadMetadata("file", "notes.txt", "text/plain", 5)
    request = uploads._DownloadRequest(
        AppSettings.model_construct(
            max_upload_bytes=100,
            qq_max_download_bytes=100,
            qq_download_timeout_seconds=1,
        ),
        Client(),
        "https://example.test/notes.txt",
        tmp_path / "qq-download-test.part",
        metadata,
        time.monotonic() + 10,
    )

    with pytest.raises(ValueError, match="content type is missing"):
        uploads._fetch_attachment(request)
    assert not request.temporary_path.exists()


def test_url_download_enforces_monotonic_total_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 200

        def __init__(self) -> None:
            self.headers = {"content-type": "text/plain"}

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def iter_bytes(self, _: int) -> tuple[bytes]:
            return (b"notes",)

    class Client(uploads.httpx2.Client):
        def stream(self, *_: object, **__: object) -> Response:
            return Response()

    clock = iter((0.0, 2.0))
    monkeypatch.setattr(uploads.time, "monotonic", lambda: next(clock))
    settings = AppSettings.model_construct(
        max_upload_bytes=100,
        qq_max_download_bytes=100,
        qq_download_timeout_seconds=10,
    )
    metadata = uploads.UploadMetadata("file", "notes.txt", "text/plain", 5)
    request = uploads._DownloadRequest(
        settings,
        Client(),
        "https://example.test/notes.txt",
        tmp_path / "qq-download-test.part",
        metadata,
        1.0,
    )

    with pytest.raises(TimeoutError, match="deadline exceeded"):
        uploads._fetch_attachment(request)
    assert not request.temporary_path.exists()


def test_url_download_initial_dns_consumes_total_deadline(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    resolutions: list[str] = []

    def monotonic() -> float:
        return now

    def resolve(
        hostname: str,
        *_: object,
        **__: object,
    ) -> list[tuple[object, object, object, object, tuple[str, int]]]:
        nonlocal now
        resolutions.append(hostname)
        now = 2.0
        return [(0, 0, 0, "", ("8.8.8.8", 443))]

    class FailingClient:
        def __init__(self, **_: object) -> None:
            message = "HTTP client should not be created after DNS deadline"
            raise OSError(message)

    event = uploads.QQInboundEvent(
        event_id="event",
        user_openid="openid-1",
        message_id="message",
        event_type="C2C_FILE_CREATE",
        provider_file_id="file",
        filename="notes.txt",
        content_type="text/plain",
        size_bytes=5,
        file_url="https://initial.test/notes.txt",
    )
    metadata = uploads.UploadMetadata("file", "notes.txt", "text/plain", 5)
    effective = settings.model_copy(update={"qq_download_deadline_seconds": 1.0})
    monkeypatch.setattr(uploads.time, "monotonic", monotonic)
    monkeypatch.setattr(uploads.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(uploads.httpx2, "Client", FailingClient)

    with pytest.raises(ValueError, match="download failed"):
        uploads.download_attachment(
            effective,
            DeterministicIdGenerator([UUID("00000000-0000-0000-0000-000000000001")]),
            event,
            metadata,
        )

    upload_dir = effective.data_dir / "uploads"
    assert resolutions == ["initial.test"]
    assert not upload_dir.exists() or not tuple(upload_dir.iterdir())


def test_url_download_redirect_dns_exhausts_remaining_deadline(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    resolutions: list[str] = []
    fetches = 0

    def monotonic() -> float:
        return now

    def resolve(
        hostname: str,
        *_: object,
        **__: object,
    ) -> list[tuple[object, object, object, object, tuple[str, int]]]:
        nonlocal now
        resolutions.append(hostname)
        now = 0.4 if hostname == "initial.test" else 2.0
        return [(0, 0, 0, "", ("8.8.8.8", 443))]

    class Client:
        def __init__(self, **_: object) -> None:
            return None

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def fetch(_: uploads._DownloadRequest) -> tuple[str | None, int | None]:
        nonlocal fetches, now
        fetches += 1
        if fetches == 1:
            now = 0.8
            return "https://redirect.test/notes.txt", None
        message = "HTTP fetch should not continue after redirect DNS deadline"
        raise OSError(message)

    event = uploads.QQInboundEvent(
        event_id="event",
        user_openid="openid-1",
        message_id="message",
        event_type="C2C_FILE_CREATE",
        provider_file_id="file",
        filename="notes.txt",
        content_type="text/plain",
        size_bytes=5,
        file_url="https://initial.test/notes.txt",
    )
    metadata = uploads.UploadMetadata("file", "notes.txt", "text/plain", 5)
    effective = settings.model_copy(update={"qq_download_deadline_seconds": 1.0})
    monkeypatch.setattr(uploads.time, "monotonic", monotonic)
    monkeypatch.setattr(uploads.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(uploads.httpx2, "Client", Client)
    monkeypatch.setattr(uploads, "_PinnedTransport", lambda _: None)
    monkeypatch.setattr(uploads, "_fetch_attachment", fetch)

    with pytest.raises(ValueError, match="download failed"):
        uploads.download_attachment(
            effective,
            DeterministicIdGenerator([UUID("00000000-0000-0000-0000-000000000001")]),
            event,
            metadata,
        )

    upload_dir = effective.data_dir / "uploads"
    assert resolutions == ["initial.test", "redirect.test"]
    assert fetches == 1
    assert not upload_dir.exists() or not tuple(upload_dir.iterdir())


def test_url_download_removes_final_path_when_chmod_fails(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    event = uploads.QQInboundEvent(
        event_id="event",
        user_openid="openid-1",
        message_id="message",
        event_type="C2C_FILE_CREATE",
        provider_file_id="file",
        filename="notes.txt",
        content_type="text/plain",
        size_bytes=5,
        file_url="https://example.test/notes.txt",
    )
    metadata = uploads.UploadMetadata("file", "notes.txt", "text/plain", 5)
    original_chmod = Path.chmod

    def fail_final_chmod(path: Path, mode: int) -> None:
        if path.suffix == ".txt":
            message = "forced chmod failure"
            raise OSError(message)
        original_chmod(path, mode)

    def write_attachment(request: uploads._DownloadRequest) -> tuple[None, int]:
        request.temporary_path.write_bytes(b"notes")
        return None, 5

    monkeypatch.setattr(
        uploads,
        "_resolve_attachment_url",
        lambda _, __=None: uploads._ValidatedAttachment("203.0.113.1", 443),
    )
    monkeypatch.setattr(uploads.httpx2, "Client", lambda **_: Client())
    monkeypatch.setattr(uploads, "_fetch_attachment", write_attachment)
    monkeypatch.setattr(Path, "chmod", fail_final_chmod)

    with pytest.raises(ValueError, match="download failed"):
        uploads.download_attachment(
            settings,
            DeterministicIdGenerator([UUID("00000000-0000-0000-0000-000000000001")]),
            event,
            metadata,
        )

    upload_dir = settings.data_dir / "uploads"
    assert not tuple(upload_dir.iterdir())


def test_url_download_rejects_pdf_without_pdf_header(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    event = uploads.QQInboundEvent(
        event_id="event",
        user_openid="openid-1",
        message_id="message",
        event_type="C2C_FILE_CREATE",
        provider_file_id="file",
        filename="notes.pdf",
        content_type="application/octet-stream",
        size_bytes=10,
        file_url="https://example.test/notes.pdf",
    )
    metadata = uploads.UploadMetadata("file", "notes.pdf", "application/octet-stream", 10)

    def write_attachment(request: uploads._DownloadRequest) -> tuple[None, int]:
        request.temporary_path.write_bytes(b"not a pdf!!")
        return None, 10

    monkeypatch.setattr(
        uploads,
        "_resolve_attachment_url",
        lambda _, __=None: uploads._ValidatedAttachment("203.0.113.1", 443),
    )
    monkeypatch.setattr(uploads.httpx2, "Client", lambda **_: Client())
    monkeypatch.setattr(uploads, "_fetch_attachment", write_attachment)

    with pytest.raises(ValueError, match="content"):
        uploads.download_attachment(
            settings,
            DeterministicIdGenerator([UUID("00000000-0000-0000-0000-000000000001")]),
            event,
            metadata,
        )

    assert not tuple((settings.data_dir / "uploads").iterdir())


def test_url_download_accepts_pdf_header(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    content = b"%PDF-1.7\n"
    event = uploads.QQInboundEvent(
        event_id="event",
        user_openid="openid-1",
        message_id="message",
        event_type="C2C_FILE_CREATE",
        provider_file_id="file",
        filename="notes.pdf",
        content_type="application/octet-stream",
        size_bytes=len(content),
        file_url="https://example.test/notes.pdf",
    )
    metadata = uploads.UploadMetadata("file", "notes.pdf", "application/octet-stream", len(content))

    def write_attachment(request: uploads._DownloadRequest) -> tuple[None, int]:
        request.temporary_path.write_bytes(content)
        return None, len(content)

    monkeypatch.setattr(
        uploads,
        "_resolve_attachment_url",
        lambda _, __=None: uploads._ValidatedAttachment("203.0.113.1", 443),
    )
    monkeypatch.setattr(uploads.httpx2, "Client", lambda **_: Client())
    monkeypatch.setattr(uploads, "_fetch_attachment", write_attachment)

    upload = uploads.download_attachment(
        settings,
        DeterministicIdGenerator([UUID("00000000-0000-0000-0000-000000000001")]),
        event,
        metadata,
    )

    assert Path(upload.path).read_bytes() == content


def test_url_download_local_server_preserves_final_bytes_and_ignores_proxy(
    settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"local success"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        event = uploads.QQInboundEvent(
            event_id="event",
            user_openid="openid-1",
            message_id="message",
            event_type="C2C_FILE_CREATE",
            provider_file_id="file",
            filename="notes.txt",
            content_type="text/plain",
            size_bytes=len(content),
            file_url=f"http://download.test:{server.server_port}/notes.txt",
        )
        metadata = uploads.UploadMetadata("file", "notes.txt", "text/plain", len(content))
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setattr(
            uploads,
            "_resolve_attachment_url",
            lambda _, __=None: uploads._ValidatedAttachment("127.0.0.1", server.server_port),
        )

        upload = uploads.download_attachment(
            settings,
            DeterministicIdGenerator([UUID("00000000-0000-0000-0000-000000000001")]),
            event,
            metadata,
        )

        path = Path(upload.path)
        assert path.exists()
        assert path.read_bytes() == content
        assert path.stat().st_mode & 0o777 == 0o600
        assert not tuple((settings.data_dir / "uploads").glob("*.part"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_attachment_url_rejects_explicit_port_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(
        *_: object,
        **__: object,
    ) -> list[tuple[object, object, object, object, tuple[str, int]]]:
        return [(0, 0, 0, "", ("8.8.8.8", 0))]

    monkeypatch.setattr(uploads.socket, "getaddrinfo", resolve)

    with pytest.raises(ValueError, match="port is invalid"):
        validate_attachment_url("https://example.test:0/file.txt")


@pytest.mark.anyio
async def test_malformed_json_and_invalid_signature_are_rejected() -> None:
    transport = StarletteEventTransport("app-id", "test-app-secret", _noop_event)

    malformed = await transport.handle_request(WebhookRequest(body=b"{", headers={}))
    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    invalid = await transport.handle_request(
        WebhookRequest(
            body=body,
            headers={
                "X-Signature-Timestamp": "1700000000",
                "X-Signature-Ed25519": "00",
            },
        )
    )

    assert malformed.status == 400
    assert invalid.status == 401


@pytest.mark.anyio
async def test_event_ack_waits_for_durable_receipt() -> None:
    receipt_ready = anyio.Event()
    callback_started = anyio.Event()

    async def persist(_: bytes, __: Mapping[str, str]) -> DurableIngestionReceipt:
        callback_started.set()
        await receipt_ready.wait()
        return DurableIngestionReceipt.accepted_receipt()

    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    transport = StarletteEventTransport("app-id", "test-app-secret", persist)

    async with anyio.create_task_group() as task_group:
        response_holder: list[int] = []

        async def request() -> None:
            response = await transport.handle_request(_signed_request(body, "test-app-secret"))
            response_holder.append(response.status)

        task_group.start_soon(request)
        await callback_started.wait()
        await anyio.lowlevel.checkpoint()
        assert response_holder == []
        receipt_ready.set()
        await anyio.lowlevel.checkpoint()
        task_group.cancel_scope.cancel()

    assert response_holder == [200]


@pytest.mark.anyio
async def test_persistence_failure_does_not_return_success_ack() -> None:
    async def persist(_: bytes, __: Mapping[str, str]) -> DurableIngestionReceipt:
        return DurableIngestionReceipt.failed_receipt(503)

    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    transport = StarletteEventTransport("app-id", "test-app-secret", persist)

    response = await transport.handle_request(_signed_request(body, "test-app-secret"))

    assert response.status == 503
    assert json.loads(response.body) == {"error": "persistence failed"}


@pytest.mark.anyio
async def test_persistence_callback_exception_returns_non_success_without_ack() -> None:
    async def persist(_: bytes, __: Mapping[str, str]) -> DurableIngestionReceipt:
        message = "persistence callback failed"
        raise RuntimeError(message)

    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    transport = StarletteEventTransport("app-id", "test-app-secret", persist)

    response = await transport.handle_request(_signed_request(body, "test-app-secret"))

    assert response.status == 503
    assert json.loads(response.body) == {"error": "persistence failed"}


@pytest.mark.anyio
async def test_request_after_close_is_rejected_before_persistence() -> None:
    calls: list[bytes] = []

    async def persist(body: bytes, _: Mapping[str, str]) -> DurableIngestionReceipt:
        calls.append(body)
        return DurableIngestionReceipt.accepted_receipt()

    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    transport = StarletteEventTransport("app-id", "test-app-secret", persist)
    await transport.close()

    response = await transport.handle_request(_signed_request(body, "test-app-secret"))

    assert response.status == 503
    assert json.loads(response.body) == {"error": "transport closed"}
    assert calls == []


@pytest.mark.anyio
async def test_handler_failure_after_receipt_still_returns_sdk_ack_and_reports_error() -> None:
    reported: list[Exception] = []

    async def report(error: Exception) -> None:
        reported.append(error)

    async def fail_handler(_: RawEvent) -> None:
        message = "post-receipt handler failed"
        raise RuntimeError(message)

    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    transport = StarletteEventTransport(
        "app-id",
        "test-app-secret",
        _noop_event,
        on_handler_error=report,
    )
    transport._handler = fail_handler

    response = await transport.handle_request(_signed_request(body, "test-app-secret"))

    assert response.status == 200
    assert json.loads(response.body) == {"op": 12, "d": 0}
    assert [str(error) for error in reported] == ["post-receipt handler failed"]


@pytest.mark.anyio
async def test_success_ack_does_not_wait_for_blocked_post_receipt_handler() -> None:
    handler_started = anyio.Event()
    handler_release = anyio.Event()
    response_ready = anyio.Event()
    response_holder: list[int] = []

    async def blocked_handler(_: RawEvent) -> None:
        handler_started.set()
        await handler_release.wait()

    async def request(transport: StarletteEventTransport, body: bytes) -> None:
        response = await transport.handle_request(_signed_request(body, "test-app-secret"))
        response_holder.append(response.status)
        response_ready.set()

    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    transport = StarletteEventTransport("app-id", "test-app-secret", _noop_event)
    transport._handler = blocked_handler

    async with anyio.create_task_group() as task_group:
        transport.bind_handler_task_group(task_group)
        task_group.start_soon(request, transport, body)
        await handler_started.wait()
        with anyio.fail_after(1):
            await response_ready.wait()
        await transport.close()
        handler_release.set()
        task_group.cancel_scope.cancel()

    assert response_holder == [200]


@pytest.mark.anyio
async def test_transport_close_cancels_pending_post_receipt_handler() -> None:
    handler_started = anyio.Event()
    handler_cancelled = anyio.Event()

    async def blocked_handler(_: RawEvent) -> None:
        handler_started.set()
        try:
            await anyio.sleep_forever()
        finally:
            handler_cancelled.set()

    body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}}).encode()
    transport = StarletteEventTransport("app-id", "test-app-secret", _noop_event)
    transport._handler = blocked_handler

    async with anyio.create_task_group() as task_group:
        transport.bind_handler_task_group(task_group)
        task_group.start_soon(
            transport.handle_request,
            _signed_request(body, "test-app-secret"),
        )
        await handler_started.wait()
        await transport.close()
        with anyio.fail_after(1):
            await handler_cancelled.wait()
        task_group.cancel_scope.cancel()


@pytest.mark.anyio
async def test_runtime_closes_transport_and_client_in_order() -> None:
    close_order: list[str] = []

    class FakeClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> None:
            assert (appid, secret, ret_coro) == ("app-id", "secret", True)

        async def close(self) -> None:
            close_order.append("client")

    class FakeTransport:
        async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
            return WebhookResponse(200, request.body)

        async def wait_ready(self) -> None:
            return None

        async def close(self) -> None:
            close_order.append("transport")

    runtime = LifespanBotpyRuntime(FakeClient(), FakeTransport(), "app-id", "secret")
    await runtime.close()

    assert close_order == ["client", "transport"]


@pytest.mark.anyio
async def test_runtime_close_is_idempotent() -> None:
    close_counts = {"client": 0, "transport": 0}

    class FakeClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> None:
            return None

        async def close(self) -> None:
            close_counts["client"] += 1

    class FakeTransport:
        async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
            return WebhookResponse(200, request.body)

        async def wait_ready(self) -> None:
            return None

        async def close(self) -> None:
            close_counts["transport"] += 1

    runtime = LifespanBotpyRuntime(FakeClient(), FakeTransport(), "app-id", "secret")
    await runtime.close()
    await runtime.close()

    assert close_counts == {"client": 1, "transport": 1}


@pytest.mark.anyio
async def test_runtime_waits_for_transport_readiness_before_returning() -> None:
    ready = anyio.Event()
    handled = anyio.Event()

    class FakeClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> Awaitable[None]:
            async def sdk_transport() -> None:
                ready.set()
                await anyio.sleep_forever()

            return sdk_transport()

        async def close(self) -> None:
            return None

    class FakeTransport:
        async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
            if not ready.is_set():
                message = "transport was not ready"
                raise RuntimeError(message)
            handled.set()
            return WebhookResponse(200, request.body)

        async def wait_ready(self) -> None:
            await ready.wait()

        async def close(self) -> None:
            return None

    runtime = LifespanBotpyRuntime(FakeClient(), FakeTransport(), "app-id", "secret")
    async with anyio.create_task_group() as task_group:
        await runtime.start(task_group)
        response = await runtime.handle_http_request(b"ready", {})
        await handled.wait()
        assert response.status == 200
        task_group.cancel_scope.cancel()


@pytest.mark.anyio
async def test_runtime_close_completes_when_shutdown_is_cancelled() -> None:
    client_close_started = anyio.Event()
    release_client_close = anyio.Event()
    close_order: list[str] = []

    class FakeClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> None:
            return None

        async def close(self) -> None:
            client_close_started.set()
            await release_client_close.wait()
            close_order.append("client")

    class FakeTransport:
        async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
            return WebhookResponse(200, request.body)

        async def wait_ready(self) -> None:
            return None

        async def close(self) -> None:
            close_order.append("transport")

    runtime = LifespanBotpyRuntime(FakeClient(), FakeTransport(), "app-id", "secret")
    with anyio.CancelScope() as cancel_scope:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(runtime.close)
            await client_close_started.wait()
            cancel_scope.cancel()
            release_client_close.set()

    assert close_order == ["client", "transport"]


@pytest.mark.anyio
async def test_lifecycle_cleanup_attempts_owned_and_database_after_runtime_failure() -> None:
    cleanup_order: list[str] = []

    class FailingClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> None:
            return None

        async def close(self) -> None:
            cleanup_order.append("client")
            message = "client close failed"
            raise RuntimeError(message)

    class FakeTransport:
        async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
            return WebhookResponse(200, request.body)

        async def wait_ready(self) -> None:
            return None

        async def close(self) -> None:
            cleanup_order.append("transport")

    class FakeOwned:
        def close(self) -> None:
            cleanup_order.append("owned")

    class FakeDatabase:
        def dispose(self) -> None:
            cleanup_order.append("database")

    runtime = LifespanBotpyRuntime(FailingClient(), FakeTransport(), "app-id", "secret")
    with pytest.raises(RuntimeError, match="client close failed"):
        await _cleanup_lifecycle_resources(runtime, FakeOwned(), FakeDatabase())

    assert cleanup_order == ["client", "transport", "owned", "database"]


@pytest.mark.anyio
async def test_lifecycle_cleanup_attempts_database_after_owned_close_failure() -> None:
    cleanup_order: list[str] = []

    class FailingOwned:
        def close(self) -> None:
            cleanup_order.append("owned")
            message = "owned close failed"
            raise RuntimeError(message)

    class FakeDatabase:
        def dispose(self) -> None:
            cleanup_order.append("database")

    with pytest.raises(RuntimeError, match="owned close failed"):
        await _cleanup_lifecycle_resources(None, FailingOwned(), FakeDatabase())

    assert cleanup_order == ["owned", "database"]


@pytest.mark.anyio
async def test_runtime_start_failure_still_closes_owned_resources() -> None:
    close_order: list[str] = []

    class FakeClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> None:
            message = "startup failed"
            raise RuntimeError(message)

        async def close(self) -> None:
            close_order.append("client")

    class FakeTransport:
        async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
            return WebhookResponse(200, request.body)

        async def wait_ready(self) -> None:
            return None

        async def close(self) -> None:
            close_order.append("transport")

    runtime = LifespanBotpyRuntime(FakeClient(), FakeTransport(), "app-id", "secret")
    with pytest.raises(RuntimeError, match="startup failed"):
        await runtime.start()
    await runtime.close()

    assert close_order == ["client", "transport"]


@pytest.mark.anyio
async def test_runtime_rejects_duplicate_start_without_second_listener() -> None:
    starts = 0

    class FakeClient:
        async def start(self, appid: str, secret: str, ret_coro: bool) -> None:
            nonlocal starts
            starts += 1

        async def close(self) -> None:
            return None

    class FakeTransport:
        async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
            return WebhookResponse(200, request.body)

        async def wait_ready(self) -> None:
            return None

        async def close(self) -> None:
            return None

    runtime = LifespanBotpyRuntime(FakeClient(), FakeTransport(), "app-id", "secret")
    await runtime.start()

    with pytest.raises(RuntimeError, match="already started"):
        await runtime.start()

    assert starts == 1
