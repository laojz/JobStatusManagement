"""Credential-gated botpy transport and lifespan resource ownership."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol

import anyio
import botpy
import structlog
from botpy.protocol.errors import ApiError, TransportError
from botpy.protocol.events import parse_gateway_event
from botpy.protocol.message import MediaSendResult, MessageType
from botpy.protocol.models import RawEvent
from botpy.protocol.models import ReplyTarget as SdkReplyTarget
from botpy.protocol.transport import (
    EventHandler,
    WebhookRequest,
    WebhookResponse,
    sign_validation_response,
    verify_webhook_signature,
)
from botpy.types.message import Message

from jobs_status_manager.agent.contracts import (
    ProviderError,
    ProviderErrorKind,
    QQInboundEvent,
    ReplyMode,
    ReplyTarget,
)
from jobs_status_manager.infrastructure.clock import Clock, SystemClock

if TYPE_CHECKING:
    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.infrastructure.adapters.protocols import QQGateway


logger = structlog.get_logger(__name__)
HeaderValue = str | list[str] | tuple[str, ...]
DurableEventHandler = Callable[[bytes, Mapping[str, str]], Awaitable["DurableIngestionReceipt"]]
HandlerErrorReporter = Callable[[Exception], Awaitable[None]]
OP_DISPATCH: Final = 0
OP_VALIDATION: Final = 13
HTTP_BAD_REQUEST: Final = 400
HTTP_REQUEST_TIMEOUT: Final = 408
HTTP_TOO_EARLY: Final = 425
HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_SERVER_ERROR: Final = 500
DEFAULT_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS: Final = 300
VALIDATION_SIGNATURE_CACHE_LIMIT: Final = 1024
_BOTPY_CLIENT_CLASS: Final = botpy.Client


type BotpySdkResult = Message | MediaSendResult


class BotpyOutboundClient(Protocol):
    """SDK client methods used by the production outbound adapter."""

    async def send(
        self,
        target: SdkReplyTarget,
        *,
        content: str | None = None,
        msg_type: int | MessageType | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> Message:
        """Send one text or media-reference message."""
        ...

    async def send_image(
        self,
        target: SdkReplyTarget,
        *,
        data: bytes,
    ) -> MediaSendResult:
        """Upload and send one image."""
        ...

    async def send_file(
        self,
        target: SdkReplyTarget,
        *,
        data: bytes,
        file_name: str,
    ) -> MediaSendResult:
        """Upload and send one ordinary file."""
        ...


class AsyncOperationRunner(Protocol):
    """Synchronous runner for one awaitable operation."""

    def __call__(self, operation: Awaitable[BotpySdkResult]) -> BotpySdkResult:
        """Run an awaitable to completion."""
        ...


@dataclass(frozen=True, slots=True)
class BotpyDeliveryResult:
    """Provider-neutral result returned by the botpy outbound adapter."""

    success: bool
    provider_message_id: str | None = None
    provider_error: ProviderError | None = None


@dataclass(frozen=True, slots=True)
class _UnsupportedTargetError(ValueError):
    """Target cannot be represented by the QQ C2C API."""

    reason: str

    def __str__(self) -> str:
        return self.reason


class _ValidationSignatureCache:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._digests: deque[str] = deque()
        self._digest_set: set[str] = set()

    def remember(self, signature: str) -> None:
        digest = sha256(signature.encode("utf-8")).hexdigest()
        if digest in self._digest_set:
            return
        if len(self._digests) >= self._limit:
            evicted = self._digests.popleft()
            self._digest_set.remove(evicted)
        self._digests.append(digest)
        self._digest_set.add(digest)

    def contains(self, signature: str) -> bool:
        digest = sha256(signature.encode("utf-8")).hexdigest()
        return digest in self._digest_set


class BotpyQQGateway:
    """Synchronous application gateway over one lifecycle-owned botpy client."""

    def __init__(
        self,
        client: BotpyOutboundClient,
        configured_openid: str,
        timeout_seconds: float,
        runner: AsyncOperationRunner | None = None,
    ) -> None:
        """Bind one SDK client and a single configured internal-user mapping."""
        self._client = client
        self._configured_openid = configured_openid
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._closed = False

    def close(self) -> None:
        """Reject new sends before lifecycle shutdown closes the SDK client."""
        self._closed = True

    def push(self, user_id: str, content: str) -> BotpyDeliveryResult:
        """Send a proactive C2C text using the configured single-user mapping."""
        del user_id
        return self._send_text(
            ReplyTarget(
                mode=ReplyMode.PROACTIVE,
                provider_name="qq",
                provider_scope="c2c",
                target_id=self._configured_openid,
            ),
            content,
        )

    def reply(self, user_id: str, message_id: str, content: str) -> BotpyDeliveryResult:
        """Reject an incomplete passive reply instead of downgrading it."""
        del user_id, message_id, content
        return _classified_result(ProviderErrorKind.PERMANENT, "passive_target_requires_event_id")

    def deliver(self, target: ReplyTarget, content: str) -> BotpyDeliveryResult:
        """Send text to a passive or proactive provider-neutral target."""
        return self._send_text(target, content)

    def send_image(
        self,
        target: ReplyTarget,
        content_type: str,
        content: bytes,
    ) -> BotpyDeliveryResult:
        """Upload and send image bytes through botpy 2.0.4."""
        del content_type
        if self._closed:
            return _permanent_result()
        try:
            sdk_target = _sdk_target(target)
        except _UnsupportedTargetError:
            return _permanent_result()
        return self._execute(
            self._client.send_image(sdk_target, data=content),
        )

    def send_file(
        self,
        target: ReplyTarget,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> BotpyDeliveryResult:
        """Upload and send ordinary file bytes through botpy 2.0.4."""
        del content_type
        if self._closed:
            return _permanent_result()
        try:
            sdk_target = _sdk_target(target)
        except _UnsupportedTargetError:
            return _permanent_result()
        return self._execute(
            self._client.send_file(sdk_target, data=content, file_name=filename),
        )

    def validate_webhook(self, token: str | None, body: bytes) -> bool:
        """Remain compatible with the local gateway protocol; botpy validates signatures."""
        del token, body
        return False

    def receive(self, body: bytes) -> QQInboundEvent:
        """Reject direct parsing because botpy transport owns inbound normalization."""
        del body
        message = "botpy transport owns inbound event parsing"
        raise ValueError(message)

    def _send_text(self, target: ReplyTarget, content: str) -> BotpyDeliveryResult:
        if self._closed:
            return _permanent_result()
        try:
            sdk_target = _sdk_target(target)
            extra = {"msg_seq": target.msg_seq} if target.msg_seq is not None else {}
        except _UnsupportedTargetError:
            return _permanent_result()
        return self._execute(
            self._client.send(
                sdk_target,
                content=content,
                msg_type=MessageType.TEXT,
                extra=extra,
            )
        )

    def _execute(self, operation: Awaitable[BotpySdkResult]) -> BotpyDeliveryResult:
        try:
            result = self._run_with_timeout(operation)
        except ApiError as error:
            return _api_error_result(error)
        except (TransportError, TimeoutError):
            return _ambiguous_result()
        except Exception:  # noqa: BLE001,BROAD_EXCEPT_OK
            return _ambiguous_result()
        return BotpyDeliveryResult(success=True, provider_message_id=_provider_message_id(result))

    def _run_with_timeout(self, operation: Awaitable[BotpySdkResult]) -> BotpySdkResult:
        async def bounded() -> BotpySdkResult:
            with anyio.fail_after(self._timeout_seconds):
                return await operation

        if self._runner is not None:
            return self._runner(bounded())
        return anyio.from_thread.run(bounded)


def _sdk_target(target: ReplyTarget) -> SdkReplyTarget:
    if target.provider_name != "qq" or target.provider_scope != "c2c":
        reason = "only QQ C2C targets are supported"
        raise _UnsupportedTargetError(reason)
    return SdkReplyTarget(
        scope="c2c",
        target_id=target.target_id,
        message_id=target.message_id if target.mode is ReplyMode.PASSIVE else None,
        event_id=target.event_id if target.mode is ReplyMode.PASSIVE else None,
    )


def _provider_message_id(result: BotpySdkResult) -> str | None:
    match result:
        case MediaSendResult(message=message) if isinstance(message, Mapping):
            value = message.get("id")
            return value if isinstance(value, str) else None
        case MediaSendResult():
            return None
        case _:
            return result.get("id")


def _api_error_result(error: ApiError) -> BotpyDeliveryResult:
    status = error.status
    if status in {
        HTTP_REQUEST_TIMEOUT,
        HTTP_TOO_EARLY,
        HTTP_TOO_MANY_REQUESTS,
    } or (status is not None and status >= HTTP_SERVER_ERROR):
        return _classified_result(ProviderErrorKind.RETRYABLE, "api_retryable")
    if status is not None and HTTP_BAD_REQUEST <= status < HTTP_SERVER_ERROR:
        return _classified_result(ProviderErrorKind.PERMANENT, "api_rejected")
    return _ambiguous_result()


def _classified_result(kind: ProviderErrorKind, code: str) -> BotpyDeliveryResult:
    return BotpyDeliveryResult(
        success=False,
        provider_error=ProviderError(kind=kind, provider_name="qq", code=code),
    )


def _permanent_result() -> BotpyDeliveryResult:
    return _classified_result(ProviderErrorKind.PERMANENT, "unsupported_or_shutdown")


def _ambiguous_result() -> BotpyDeliveryResult:
    return _classified_result(ProviderErrorKind.AMBIGUOUS, "unknown_or_timeout")


@dataclass(frozen=True, slots=True)
class DurableIngestionReceipt:
    """Application persistence result used as the ordinary-event ACK barrier."""

    accepted: bool
    status_code: int
    body: bytes

    @classmethod
    def accepted_receipt(cls) -> DurableIngestionReceipt:
        """Create the receipt that permits an ordinary event ACK."""
        return cls(accepted=True, status_code=202, body=b'{"status":"accepted"}')

    @classmethod
    def failed_receipt(cls, status_code: int) -> DurableIngestionReceipt:
        """Create a non-success receipt that requests provider redelivery."""
        return cls(accepted=False, status_code=status_code, body=b'{"error":"persistence failed"}')


@dataclass(frozen=True, slots=True)
class HttpTransportResponse:
    """HTTP response shape exposed outside the botpy infrastructure boundary."""

    status: int
    body: bytes
    headers: Mapping[str, str]


class BotpyClient(Protocol):
    """Subset of the SDK client owned by the application lifecycle."""

    async def start(self, appid: str, secret: str, ret_coro: bool) -> Awaitable[None] | None:
        """Log in and return the SDK transport coroutine when requested."""
        ...

    async def close(self) -> None:
        """Close SDK HTTP and transport resources."""
        ...


class RequestTransport(Protocol):
    """Transport surface used by the Starlette route boundary."""

    async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
        """Handle one request without owning a listening socket."""
        ...

    async def wait_ready(self) -> None:
        """Wait until the transport has installed its SDK handler."""
        ...

    async def close(self) -> None:
        """Stop accepting requests and release transport resources."""
        ...


class StarletteEventTransport(RequestTransport):
    """SDK-compatible webhook transport whose ACK waits for app persistence."""

    def __init__(  # noqa: PLR0913
        self,
        app_id: str,
        app_secret: str,
        on_event: DurableEventHandler,
        on_handler_error: HandlerErrorReporter | None = None,
        *,
        dispatch_timestamp_max_age_seconds: int = DEFAULT_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        """Create a transport that delegates listening to Starlette."""
        if not app_id or not app_secret:
            message = "botpy app credentials are required"
            raise ValueError(message)
        self.app_id = app_id
        self.app_secret = app_secret
        self._on_event = on_event
        self._on_handler_error = on_handler_error
        self._handler: EventHandler | None = None
        self._stop_event: anyio.Event | None = None
        self._ready_event: anyio.Event | None = None
        self._handler_task_group: anyio.abc.TaskGroup | None = None
        self._handler_cancel_scopes: set[anyio.CancelScope] = set()
        self._running = False
        self._closed = False
        self._dispatch_timestamp_max_age_seconds = dispatch_timestamp_max_age_seconds
        self._clock = clock or SystemClock()
        self._validation_signatures = _ValidationSignatureCache(VALIDATION_SIGNATURE_CACHE_LIMIT)

    async def start(self, handler: EventHandler) -> None:
        """Register the SDK dispatch handler without opening a server."""
        if self._running:
            message = "event transport is already running"
            raise RuntimeError(message)
        if self._closed:
            message = "event transport is closed"
            raise RuntimeError(message)
        ready_event = self._ready_event or anyio.Event()
        self._handler = handler
        self._stop_event = anyio.Event()
        self._ready_event = ready_event
        self._running = True
        ready_event.set()
        try:
            await self._stop_event.wait()
        finally:
            self._handler = None
            self._stop_event = None
            self._ready_event = None
            self._running = False

    def prepare_start(self) -> None:
        """Prepare the readiness handshake before the SDK coroutine is scheduled."""
        self._ready_event = anyio.Event()

    def bind_handler_task_group(self, task_group: anyio.abc.TaskGroup) -> None:
        """Bind post-receipt handlers to the lifecycle-owned task group."""
        self._handler_task_group = task_group

    async def wait_ready(self) -> None:
        """Wait until the SDK handler is installed and requests are accepted."""
        if self._closed:
            message = "event transport is closed"
            raise RuntimeError(message)
        ready_event = self._ready_event
        if ready_event is None:
            message = "event transport is not started"
            raise RuntimeError(message)
        await ready_event.wait()

    async def close(self) -> None:
        """Stop the in-process transport without touching a network listener."""
        if self._closed:
            return
        self._closed = True
        for cancel_scope in tuple(self._handler_cancel_scopes):
            cancel_scope.cancel()
        if self._stop_event is not None:
            self._stop_event.set()

    async def handle_request(self, request: WebhookRequest) -> WebhookResponse:
        """Validate one SDK webhook request and await its durable receipt."""
        if self._closed:
            return self._json_response(503, {"error": "transport closed"})
        payload = self._decode_payload(request.body)
        if payload is None:
            return self._json_response(400, {"error": "invalid json"})
        operation = payload.get("op")
        if isinstance(operation, bool) or not isinstance(operation, int):
            return self._json_response(400, {"error": "invalid payload"})
        if operation == OP_VALIDATION:
            return self._validation_response(payload)
        if not self._valid_signature(request):
            return self._json_response(401, {"error": "invalid signature"})
        if operation == OP_DISPATCH:
            response = await self._dispatch_request(request, payload)
        else:
            response = self._json_response(200, {"op": 12, "d": 0})
        return response

    async def _dispatch_request(
        self,
        request: WebhookRequest,
        payload: Mapping[str, object],
    ) -> WebhookResponse:
        """Persist one dispatch and run optional post-receipt SDK handling."""
        try:
            receipt = await self._on_event(request.body, self._string_headers(request.headers))
        except Exception as error:
            logger.exception("qq_event_persistence_failed", error=type(error).__name__)
            return self._json_response(503, {"error": "persistence failed"})
        if not receipt.accepted:
            return WebhookResponse(receipt.status_code, receipt.body)
        handler = self._handler
        if handler is not None:
            parsed_event = parse_gateway_event(payload)
            task_group = self._handler_task_group
            if task_group is None:
                await self._run_handler(handler, parsed_event)
            else:
                task_group.start_soon(self._run_handler, handler, parsed_event)
        return self._json_response(200, {"op": 12, "d": 0})

    async def _run_handler(self, handler: EventHandler, event: RawEvent) -> None:
        with anyio.CancelScope() as cancel_scope:
            self._handler_cancel_scopes.add(cancel_scope)
            try:
                await handler(event)
            except Exception as error:  # noqa: BLE001
                await self._report_handler_error(error)
            finally:
                self._handler_cancel_scopes.discard(cancel_scope)

    async def _report_handler_error(self, error: Exception) -> None:
        """Report post-receipt SDK dispatch failures without changing the ACK."""
        if self._on_handler_error is None:
            logger.error("qq_event_handler_failed", error=type(error).__name__)
            return
        try:
            await self._on_handler_error(error)
        except Exception as reporter_error:
            logger.exception(
                "qq_event_handler_error_reporter_failed",
                error=type(reporter_error).__name__,
            )

    @staticmethod
    def _decode_payload(body: bytes) -> dict[str, object] | None:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _valid_signature(self, request: WebhookRequest) -> bool:
        timestamp = self._header(request.headers, "x-signature-timestamp")
        signature = self._header(request.headers, "x-signature-ed25519")
        if timestamp is None or signature is None:
            return False
        if not self._fresh_dispatch_timestamp(timestamp):
            return False
        signature_is_valid = verify_webhook_signature(
            body=request.body,
            timestamp=timestamp,
            signature=signature,
            bot_secret=self.app_secret,
        )
        return signature_is_valid and not self._validation_signatures.contains(signature)

    def _fresh_dispatch_timestamp(self, timestamp: str) -> bool:
        try:
            timestamp_seconds = int(timestamp)
        except ValueError:
            return False
        now_seconds = self._clock.now().timestamp()
        max_age = self._dispatch_timestamp_max_age_seconds
        return now_seconds - max_age <= timestamp_seconds <= now_seconds + max_age

    def _validation_response(self, payload: Mapping[str, object]) -> WebhookResponse:
        data = payload.get("d")
        if not isinstance(data, Mapping):
            return self._json_response(400, {"error": "invalid validation"})
        plain_token = data.get("plain_token")
        event_ts = data.get("event_ts")
        if not isinstance(plain_token, str) or not plain_token:
            return self._json_response(400, {"error": "invalid validation"})
        if not isinstance(event_ts, str) or not event_ts:
            return self._json_response(400, {"error": "invalid validation"})
        response = sign_validation_response(
            plain_token=plain_token,
            event_ts=event_ts,
            bot_secret=self.app_secret,
        )
        self._validation_signatures.remember(response["signature"])
        return self._json_response(200, response)

    @staticmethod
    def _header(headers: Mapping[str, HeaderValue], name: str) -> str | None:
        for key, value in headers.items():
            if key.lower() != name:
                continue
            if isinstance(value, str):
                return value
            if value:
                return value[0]
        return None

    @classmethod
    def _string_headers(cls, headers: Mapping[str, HeaderValue]) -> dict[str, str]:
        return {key: value for key in headers for value in [cls._header(headers, key)] if value}

    @staticmethod
    def _json_response(status: int, payload: Mapping[str, str | int]) -> WebhookResponse:
        return WebhookResponse(
            status=status,
            body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )


async def _retain_async_cleanup_error(
    operation: Awaitable[None],
    first_error: BaseException | None,
) -> BaseException | None:
    try:
        await operation
    except BaseException as error:  # noqa: BLE001
        return first_error if first_error is not None else error
    return first_error


async def _close_owned_botpy_httpx_sessions(client: botpy.Client) -> None:
    cleanup_error: BaseException | None = None
    api_client = client.http._client  # noqa: SLF001
    if api_client is not None:
        if api_client._owns_session:  # noqa: SLF001
            api_session = api_client._session  # noqa: SLF001
            if api_session is not None and not api_session.is_closed:
                cleanup_error = await _retain_async_cleanup_error(
                    api_session.aclose(), cleanup_error
                )
        cleanup_error = await _retain_async_cleanup_error(api_client.close(), cleanup_error)

    token = client.http._token  # noqa: SLF001
    if token is not None:
        token_manager = token._manager  # noqa: SLF001
        if token_manager._owns_session:  # noqa: SLF001
            token_session = token_manager._session  # noqa: SLF001
            if token_session is not None and not token_session.is_closed:
                cleanup_error = await _retain_async_cleanup_error(
                    token_session.aclose(), cleanup_error
                )
        cleanup_error = await _retain_async_cleanup_error(token_manager.close(), cleanup_error)
    if cleanup_error is not None:
        raise cleanup_error


class LifespanBotpyRuntime:
    """Own exactly one SDK client and one externally-served transport."""

    def __init__(
        self,
        client: BotpyClient,
        transport: RequestTransport,
        app_id: str,
        app_secret: str,
        gateway: QQGateway | None = None,
    ) -> None:
        """Bind one client to one externally served transport."""
        self.client = client
        self.transport = transport
        self.app_id = app_id
        self.app_secret = app_secret
        self.gateway = gateway
        self._started = False
        self._closed = False

    async def handle_http_request(
        self,
        body: bytes,
        headers: Mapping[str, str],
    ) -> HttpTransportResponse:
        """Adapt Starlette raw request data without exposing SDK request models."""
        if not self._started:
            message = "botpy runtime is not ready"
            raise RuntimeError(message)
        response = await self.transport.handle_request(WebhookRequest(body=body, headers=headers))
        return HttpTransportResponse(response.status, response.body, response.headers)

    async def start(self, task_group: anyio.abc.TaskGroup | None = None) -> None:
        """Start the client once; the caller owns the returned transport task."""
        if self._started:
            message = "botpy runtime already started"
            raise RuntimeError(message)
        if self._closed:
            message = "botpy runtime is closed"
            raise RuntimeError(message)
        if isinstance(self.transport, StarletteEventTransport) and task_group is not None:
            self.transport.bind_handler_task_group(task_group)
        transport_task = await self.client.start(self.app_id, self.app_secret, ret_coro=True)
        if transport_task is None:
            await self.transport.wait_ready()
            self._started = True
            return
        if task_group is None:
            await transport_task
            await self.transport.wait_ready()
            self._started = True
            return
        if isinstance(self.transport, StarletteEventTransport):
            self.transport.prepare_start()
        task_group.start_soon(_await_transport, transport_task)
        await self.transport.wait_ready()
        self._started = True

    async def close(self) -> None:
        """Attempt every client and transport cleanup, then raise the first failure."""
        if self._closed:
            return
        self._closed = True
        cleanup_error: BaseException | None = None
        with anyio.CancelScope(shield=True):
            if isinstance(self.gateway, BotpyQQGateway):
                self.gateway.close()
            if isinstance(self.client, _BOTPY_CLIENT_CLASS):
                try:
                    await _close_owned_botpy_httpx_sessions(self.client)
                except BaseException as error:  # noqa: BLE001
                    cleanup_error = error
            try:
                await self.client.close()
            except BaseException as error:  # noqa: BLE001
                if cleanup_error is None:
                    cleanup_error = error
            try:
                await self.transport.close()
            except BaseException as error:  # noqa: BLE001
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None:
            raise cleanup_error


def create_botpy_runtime(
    settings: AppSettings,
    on_event: DurableEventHandler,
) -> LifespanBotpyRuntime:
    """Build the credential-gated SDK runtime without starting network I/O."""
    if (
        settings.qq_app_id is None
        or settings.qq_app_secret is None
        or settings.qq_token_base_url is None
    ):
        message = "complete QQ credentials and an explicit token URL are required"
        raise ValueError(message)

    transport = StarletteEventTransport(
        settings.qq_app_id,
        settings.qq_app_secret.get_secret_value(),
        on_event,
        dispatch_timestamp_max_age_seconds=settings.qq_dispatch_timestamp_max_age_seconds,
    )
    client = botpy.Client(
        intents=botpy.Intents.none(),
        timeout=settings.qq_request_timeout_seconds,
        bot_log=False,
        ext_handlers=False,
        transport=transport,
        base_url=str(settings.qq_api_base_url),
        token_base_url=str(settings.qq_token_base_url),
    )
    gateway = BotpyQQGateway(
        client,
        settings.qq_user_openid,
        settings.qq_request_timeout_seconds,
    )
    return LifespanBotpyRuntime(
        client,
        transport,
        settings.qq_app_id,
        settings.qq_app_secret.get_secret_value(),
        gateway,
    )


async def _await_transport(transport_task: Awaitable[None]) -> None:
    await transport_task
