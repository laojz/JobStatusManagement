"""QQ file upload preparation outside relational transactions."""

import base64
import ipaddress
import socket
import ssl
import time
from collections.abc import Iterable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from urllib.parse import urljoin, urlsplit

import httpcore2
import httpx2

from jobs_status_manager.agent.contracts import QQInboundEvent
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.ids import IdGenerator
from jobs_status_manager.infrastructure.paths import ensure_private_directory
from jobs_status_manager.knowledge.files import (
    UnsupportedUploadError,
    UploadMetadata,
    store_upload,
    validate_upload,
    validate_upload_content,
)


@dataclass(frozen=True, slots=True)
class StoredUpload:
    """Validated file bytes awaiting relational metadata persistence."""

    file_id: str
    metadata: UploadMetadata
    path: str


@dataclass(frozen=True, slots=True)
class _DownloadRequest:
    settings: AppSettings
    client: httpx2.Client
    url: str
    temporary_path: Path
    metadata: UploadMetadata
    deadline: float


@dataclass(frozen=True, slots=True)
class _ValidatedAttachment:
    address: str
    port: int


type _AddressInfo = tuple[
    socket.AddressFamily,
    socket.SocketKind,
    int,
    str,
    tuple[str, int] | tuple[str, int, int, int] | tuple[int, bytes],
]


class _PinnedBackend(httpcore2.SyncBackend):
    def __init__(self, address: str) -> None:
        self.address = address

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple[int, int, int]] | None = None,
    ) -> httpcore2.NetworkStream:
        del host
        return super().connect_tcp(
            self.address,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _PinnedTransport(httpx2.HTTPTransport):
    """Connect through one already-validated destination address."""

    def __init__(self, attachment: _ValidatedAttachment) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore2.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=10,
            max_keepalive_connections=10,
            keepalive_expiry=5.0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=_PinnedBackend(attachment.address),
        )


def prepare_upload(
    settings: AppSettings,
    ids: IdGenerator,
    event: QQInboundEvent,
) -> StoredUpload | None:
    """Validate, decode, and store a supported file event."""
    if event.event_type != "C2C_FILE_CREATE":
        return None
    if (
        event.provider_file_id is None
        or event.filename is None
        or event.content_type is None
        or event.size_bytes is None
        or (event.file_content_base64 is None and event.file_url is None)
    ):
        message = "file event metadata is incomplete"
        raise UnsupportedUploadError(message)
    metadata = UploadMetadata(
        event.provider_file_id,
        event.filename,
        event.content_type,
        event.size_bytes or 1,
    )
    if event.file_url is not None:
        return download_attachment(settings, ids, event, metadata)
    extension = validate_upload(metadata, settings.max_upload_bytes)
    content = base64.b64decode(event.file_content_base64 or "", validate=True)
    if len(content) != (event.size_bytes or 0):
        message = "file size does not match provider metadata"
        raise UnsupportedUploadError(message)
    validate_upload_content(metadata, content)
    file_id = str(ids.new_id())
    path = store_upload(settings.data_dir, file_id, extension, content)
    return StoredUpload(file_id, metadata, str(path))


def discard_upload(upload: StoredUpload) -> None:
    """Remove bytes whose relational persistence did not commit."""
    Path(upload.path).unlink(missing_ok=True)


def validate_attachment_url(url: str) -> None:
    """Reject non-HTTPS and private or unresolved attachment destinations."""
    _resolve_attachment_url(url)


def _resolve_attachment_url(url: str, deadline: float | None = None) -> _ValidatedAttachment:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.casefold()
        username = parsed.username
        password = parsed.password
        hostname = parsed.hostname
        port = parsed.port if parsed.port is not None else 443
    except ValueError as error:
        message = "attachment URL is invalid"
        raise UnsupportedUploadError(message) from error
    if scheme != "https" or username or password:
        message = "https required"
        raise UnsupportedUploadError(message)
    if hostname is None:
        message = "attachment host is invalid"
        raise UnsupportedUploadError(message)
    if port == 0:
        message = "attachment port is invalid"
        raise UnsupportedUploadError(message)
    addresses = _resolve_addresses(hostname, port, deadline)
    if not addresses:
        message = "attachment host cannot be resolved"
        raise UnsupportedUploadError(message)
    if deadline is not None:
        _check_deadline(deadline)
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as error:
            message = "attachment address is invalid"
            raise UnsupportedUploadError(message) from error
        if not parsed_address.is_global:
            message = "private address is not allowed"
            raise UnsupportedUploadError(message)
    return _ValidatedAttachment(addresses[0], port)


def _resolve_addresses(hostname: str, port: int, deadline: float | None) -> list[str]:
    try:
        address_info = _getaddrinfo(hostname, port, deadline)
        return [result[4][0] for result in address_info if isinstance(result[4][0], str)]
    except socket.gaierror as error:
        message = "attachment host cannot be resolved"
        raise UnsupportedUploadError(message) from error


def download_attachment(
    settings: AppSettings,
    ids: IdGenerator,
    event: QQInboundEvent,
    metadata: UploadMetadata,
) -> StoredUpload:
    """Stream a validated HTTPS attachment into private application storage."""
    url = event.file_url
    if url is None:
        message = "attachment URL is missing"
        raise UnsupportedUploadError(message)
    deadline = time.monotonic() + settings.qq_download_deadline_seconds
    max_download_bytes = min(settings.max_upload_bytes, settings.qq_max_download_bytes)
    extension = validate_upload(metadata, max_download_bytes)
    file_id = str(ids.new_id())
    directory = ensure_private_directory(settings.data_dir / "uploads")
    path = directory / f"{file_id}{extension}"
    temporary_path = directory / f"{file_id}.part"
    current_url = url
    committed = False
    try:
        for _ in range(4):
            _check_deadline(deadline)
            attachment = _resolve_attachment_url(current_url, deadline)
            with httpx2.Client(
                transport=_PinnedTransport(attachment),
                timeout=httpx2.Timeout(
                    connect=float(settings.qq_download_timeout_seconds),
                    read=float(settings.qq_download_timeout_seconds),
                    write=float(settings.qq_download_timeout_seconds),
                    pool=float(settings.qq_download_timeout_seconds),
                ),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                redirect, size = _fetch_attachment(
                    _DownloadRequest(
                        settings,
                        client,
                        current_url,
                        temporary_path,
                        metadata,
                        deadline,
                    )
                )
                if redirect is not None:
                    current_url = redirect
                    continue
                if size is None:
                    message = "attachment download failed"
                    raise UnsupportedUploadError(message)
                if event.size_bytes is not None and size != event.size_bytes:
                    message = "file size does not match provider metadata"
                    raise UnsupportedUploadError(message)
                final_metadata = UploadMetadata(
                    metadata.provider_file_id,
                    metadata.filename,
                    metadata.content_type,
                    size,
                )
                validate_upload(final_metadata, max_download_bytes)
                validate_upload_content(final_metadata, temporary_path)
                temporary_path.replace(path)
                path.chmod(0o600)
                committed = True
                return StoredUpload(file_id, final_metadata, str(path))
        message = "attachment redirects exceeded limit"
        raise UnsupportedUploadError(message)
    except (httpx2.HTTPError, TimeoutError, OSError) as error:
        message = "attachment download failed"
        raise UnsupportedUploadError(message) from error
    finally:
        temporary_path.unlink(missing_ok=True)
        if not committed:
            path.unlink(missing_ok=True)


def _getaddrinfo(
    hostname: str,
    port: int,
    deadline: float | None,
) -> list[_AddressInfo]:
    if deadline is None:
        return socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _check_deadline(deadline)

    result: Future[list[_AddressInfo]] = Future()

    def resolve() -> None:
        try:
            result.set_result(socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM))
        except socket.gaierror as error:
            result.set_exception(error)

    Thread(target=resolve, daemon=True).start()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _check_deadline(deadline)
    try:
        return result.result(timeout=remaining)
    except FutureTimeoutError as error:
        message = "attachment download deadline exceeded"
        raise TimeoutError(message) from error


def _fetch_attachment(request: _DownloadRequest) -> tuple[str | None, int | None]:
    remaining = request.deadline - time.monotonic()
    if remaining <= 0:
        _check_deadline(request.deadline)
    try:
        with request.client.stream(
            "GET",
            request.url,
            timeout=min(remaining, float(request.settings.qq_download_timeout_seconds)),
        ) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if location is None:
                    message = "attachment redirect is invalid"
                    raise UnsupportedUploadError(message)  # noqa: TRY301
                return urljoin(request.url, location), None
            if response.status_code != httpx2.codes.OK:
                message = "attachment download failed"
                raise UnsupportedUploadError(message)  # noqa: TRY301
            response_type = response.headers.get("content-type")
            if response_type is None:
                message = "attachment content type is missing"
                raise UnsupportedUploadError(message)  # noqa: TRY301
            normalized_type = response_type.split(";", 1)[0].strip().casefold()
            if normalized_type != request.metadata.content_type.casefold():
                message = "attachment content type mismatch"
                raise UnsupportedUploadError(message)  # noqa: TRY301
            size = 0
            with request.temporary_path.open("wb") as output:
                for chunk in response.iter_bytes(64 * 1024):
                    _check_deadline(request.deadline)
                    size += len(chunk)
                    if size > min(
                        request.settings.max_upload_bytes,
                        request.settings.qq_max_download_bytes,
                    ):
                        message = "file size is outside the configured limit"
                        raise UnsupportedUploadError(message)  # noqa: TRY301
                    output.write(chunk)
            return None, size
    except (UnsupportedUploadError, OSError, TimeoutError, httpx2.HTTPError):
        request.temporary_path.unlink(missing_ok=True)
        raise


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        message = "attachment download deadline exceeded"
        raise TimeoutError(message)
