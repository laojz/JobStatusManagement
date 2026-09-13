"""Typed QQ IMAP connection construction and command seam."""

from __future__ import annotations

import errno
import imaplib
import socket
import ssl
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from jobs_status_manager.config.settings import IMAP_FOLDER, IMAP_HOST, IMAP_PORT
from jobs_status_manager.infrastructure.adapters.qq_imap_cursor import (
    IMAPAuthenticationError,
    IMAPConfigurationError,
    IMAPError,
    IMAPPhase,
    IMAPProtocolError,
    IMAPTimeoutError,
    IMAPTransportError,
)

if TYPE_CHECKING:
    from pydantic import SecretStr

    from jobs_status_manager.config.settings import AppSettings


@dataclass(frozen=True, slots=True)
class QQIMAPConfig:
    """Validated settings needed by one QQ IMAP gateway."""

    account: str
    auth_code: SecretStr
    batch_size: int = 100
    connect_timeout_seconds: int = 10
    command_timeout_seconds: int = 30
    host: str = IMAP_HOST
    port: int = IMAP_PORT
    folder: str = IMAP_FOLDER

    @classmethod
    def from_settings(cls, settings: AppSettings) -> QQIMAPConfig:
        """Create the adapter configuration from validated application settings."""
        if settings.imap_account is None or settings.imap_auth_code is None:
            raise IMAPConfigurationError
        return cls(
            account=settings.imap_account,
            auth_code=settings.imap_auth_code,
            batch_size=settings.imap_batch_size,
            connect_timeout_seconds=settings.imap_connect_timeout_seconds,
            command_timeout_seconds=settings.imap_command_timeout_seconds,
            host=settings.imap_host,
            port=settings.imap_port,
            folder=settings.imap_folder,
        )


@dataclass(frozen=True, slots=True)
class ConnectionOptions:
    """Fresh connection parameters supplied to the injectable factory."""

    host: str
    port: int
    ssl_context: ssl.SSLContext
    connect_timeout_seconds: int
    command_timeout_seconds: int


class IMAPConnection(Protocol):
    def login(self, user: str, password: str) -> tuple[str, Sequence[bytes | None]]: ...

    def select(
        self, mailbox: str, readonly: bool = False
    ) -> tuple[str, Sequence[bytes | None]]: ...

    def response(self, code: str) -> tuple[str | None, list[bytes] | None]: ...

    def uid(
        self, command: str, *args: str
    ) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]: ...

    def logout(self) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]: ...

    def shutdown(self) -> None: ...


ConnectionFactory = Callable[[ConnectionOptions], IMAPConnection]


def _phase_error(error: IMAPError, phase: IMAPPhase) -> IMAPError:
    if error.phase is None:
        error.phase = phase
    return error


class _IMAPLibConnection:
    def __init__(self, connection: imaplib.IMAP4_SSL) -> None:
        self._connection = connection

    def login(self, user: str, password: str) -> tuple[str, Sequence[bytes | None]]:
        return self._connection.login(user, password)

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, Sequence[bytes | None]]:
        return self._connection.select(mailbox, readonly)

    def response(self, code: str) -> tuple[str | None, list[bytes] | None]:
        return self._connection.response(code)

    def uid(
        self, command: str, *args: str
    ) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]:
        if command == "SEARCH":
            return self._connection.uid(command, "", *args)
        return self._connection.uid(command, *args)

    def logout(self) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]:
        return self._connection.logout()

    def shutdown(self) -> None:
        self._connection.shutdown()


def default_connection_factory(options: ConnectionOptions) -> IMAPConnection:
    """Create one verified SSL connection and apply its command timeout."""
    connection = imaplib.IMAP4_SSL(
        options.host,
        options.port,
        ssl_context=options.ssl_context,
        timeout=options.connect_timeout_seconds,
    )
    connection.sock.settimeout(options.command_timeout_seconds)
    return _IMAPLibConnection(connection)


def map_error(error: BaseException, *, phase: IMAPPhase) -> IMAPError:
    """Map one expected library failure to a safe adapter error."""
    if isinstance(error, (socket.timeout, TimeoutError)):
        return IMAPTimeoutError(phase)
    if isinstance(error, (imaplib.IMAP4.abort, OSError, ssl.SSLError)):
        return IMAPTransportError(phase)
    if phase is IMAPPhase.LOGIN:
        return IMAPAuthenticationError(phase)
    return IMAPProtocolError(phase)


def cleanup_connection(connection: IMAPConnection | None) -> IMAPError | None:
    """Logout and close one connection without masking a primary error."""
    if connection is None:
        return None
    first_error: IMAPError | None = None
    try:
        response = connection.logout()
        if response[0] not in {"BYE", "OK"}:
            first_error = IMAPProtocolError(IMAPPhase.CLEANUP)
    except IMAPError as error:
        first_error = _phase_error(error, IMAPPhase.CLEANUP)
    except (
        imaplib.IMAP4.abort,
        imaplib.IMAP4.error,
        OSError,
        ssl.SSLError,
        TimeoutError,
    ) as error:
        first_error = map_error(error, phase=IMAPPhase.CLEANUP)
    try:
        connection.shutdown()
    except (OSError, ssl.SSLError, TimeoutError) as error:
        if first_error is None and not (type(error) is OSError and error.errno == errno.EBADF):
            first_error = map_error(error, phase=IMAPPhase.CLEANUP)
    return first_error
