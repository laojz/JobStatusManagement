"""Production QQ IMAP gateway and compatibility exports for IMAP primitives."""

from __future__ import annotations

import imaplib
import re
import ssl
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Final

from jobs_status_manager.config.settings import IMAP_FOLDER, IMAP_HOST, IMAP_PORT
from jobs_status_manager.infrastructure.adapters._qq_imap_connection import (
    ConnectionFactory,
    ConnectionOptions,
    IMAPConnection,
    QQIMAPConfig,
    cleanup_connection,
    default_connection_factory,
    map_error,
)
from jobs_status_manager.infrastructure.adapters.qq_imap_cursor import (
    Cursor,
    CursorClassification,
    CursorResetReason,
    IMAPAuthenticationError,
    IMAPConfigurationError,
    IMAPCursorError,
    IMAPError,
    IMAPPhase,
    IMAPProtocolError,
    IMAPTimeoutError,
    IMAPTransportError,
    classify_cursor,
    decode_cursor,
    encode_cursor,
)
from jobs_status_manager.infrastructure.adapters.qq_imap_mime import message_to_envelope
from jobs_status_manager.mail import MailEnvelope, MailPollBatch

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

_FETCH_INTERNALDATE_RE: Final = re.compile(rb"INTERNALDATE\s+\"([^\"]+)\"")
_MAX_BATCH_SIZE: Final = 500
_FETCH_PARTS: Final = 2
_IMAP_COMMAND_ERRORS: Final = (
    imaplib.IMAP4.abort,
    imaplib.IMAP4.error,
    OSError,
    ssl.SSLError,
    TimeoutError,
)


def _phase_error(error: IMAPError, phase: IMAPPhase) -> IMAPError:
    if error.phase is None:
        error.phase = phase
    return error


class QQIMAPGateway:
    """Single-account, one-shot QQ IMAP polling gateway."""

    def __init__(
        self,
        config: QQIMAPConfig,
        *,
        connection_factory: ConnectionFactory = default_connection_factory,
    ) -> None:
        """Store validated settings and the injectable connection factory."""
        self._config = config
        self._connection_factory = connection_factory

    def poll(self, account_key: str, cursor: str | None) -> MailPollBatch:
        """Poll new UID messages and return a provider-neutral batch."""
        del account_key
        self._validate_config()
        connection: IMAPConnection | None = None
        primary_error: IMAPError | None = None
        try:
            connection = self._connection_factory(
                ConnectionOptions(
                    host=self._config.host,
                    port=self._config.port,
                    ssl_context=ssl.create_default_context(),
                    connect_timeout_seconds=self._config.connect_timeout_seconds,
                    command_timeout_seconds=self._config.command_timeout_seconds,
                )
            )
            return self._poll_connection(connection, cursor)
        except IMAPError as error:
            primary_error = error
            raise
        except _IMAP_COMMAND_ERRORS as error:
            mapped = map_error(error, phase=IMAPPhase.CONNECT)
            primary_error = mapped
            raise mapped from None
        finally:
            cleanup_error = cleanup_connection(connection)
            if primary_error is None and cleanup_error is not None:
                raise cleanup_error

    def _poll_connection(self, connection: IMAPConnection, cursor: str | None) -> MailPollBatch:
        try:
            login_response = connection.login(
                self._config.account, self._config.auth_code.get_secret_value()
            )
        except IMAPError as error:
            raise _phase_error(error, IMAPPhase.LOGIN) from None
        except _IMAP_COMMAND_ERRORS as error:
            raise map_error(error, phase=IMAPPhase.LOGIN) from None
        self._expect_ok(login_response, IMAPPhase.LOGIN)
        try:
            select_response = connection.select(self._config.folder, readonly=True)
        except IMAPError as error:
            raise _phase_error(error, IMAPPhase.SELECT) from None
        except _IMAP_COMMAND_ERRORS as error:
            raise map_error(error, phase=IMAPPhase.SELECT) from None
        self._expect_ok(select_response, IMAPPhase.SELECT)
        uidvalidity = self._read_uidvalidity(connection)
        current_uids = self._search_uids(connection, "ALL")
        current_max_uid = current_uids[-1] if current_uids else 0
        classification = classify_cursor(
            cursor,
            current_uidvalidity=uidvalidity,
            current_max_uid=current_max_uid,
        )
        if classification.reset:
            return MailPollBatch(
                envelopes=(),
                next_cursor=encode_cursor(classification.cursor),
                reset=True,
            )
        new_uids = self._search_uids(connection, f"UID {classification.cursor.uid + 1}:*")
        selected_uids = new_uids[: self._config.batch_size]
        envelopes = tuple(
            self._fetch_envelope(connection, uidvalidity=uidvalidity, uid=uid)
            for uid in selected_uids
        )
        next_uid = selected_uids[-1] if selected_uids else classification.cursor.uid
        return MailPollBatch(
            envelopes=envelopes,
            next_cursor=encode_cursor(Cursor(uidvalidity=uidvalidity, uid=next_uid)),
            reset=False,
        )

    def _validate_config(self) -> None:
        if not self._config.account or not self._config.auth_code.get_secret_value():
            raise IMAPConfigurationError
        if (self._config.host, self._config.port, self._config.folder) != (
            IMAP_HOST,
            IMAP_PORT,
            IMAP_FOLDER,
        ):
            raise IMAPConfigurationError
        if self._config.batch_size < 1 or self._config.batch_size > _MAX_BATCH_SIZE:
            raise IMAPConfigurationError

    def _read_uidvalidity(self, connection: IMAPConnection) -> int:
        try:
            status, values = connection.response("UIDVALIDITY")
        except IMAPError as error:
            raise _phase_error(error, IMAPPhase.UIDVALIDITY) from None
        except _IMAP_COMMAND_ERRORS as error:
            raise map_error(error, phase=IMAPPhase.UIDVALIDITY) from None
        if status != "UIDVALIDITY" or not values or len(values) != 1:
            raise IMAPProtocolError(IMAPPhase.UIDVALIDITY)
        try:
            value = int(values[0])
        except (TypeError, ValueError):
            raise IMAPProtocolError(IMAPPhase.UIDVALIDITY) from None
        if value < 0:
            raise IMAPProtocolError(IMAPPhase.UIDVALIDITY)
        return value

    def _search_uids(self, connection: IMAPConnection, query: str) -> list[int]:
        try:
            status, values = connection.uid("SEARCH", query)
        except IMAPError as error:
            raise _phase_error(error, IMAPPhase.SEARCH) from None
        except _IMAP_COMMAND_ERRORS as error:
            raise map_error(error, phase=IMAPPhase.SEARCH) from None
        if status != "OK" or not values:
            raise IMAPProtocolError(IMAPPhase.SEARCH)
        raw = next((value for value in values if isinstance(value, bytes)), None)
        if raw is None:
            raise IMAPProtocolError(IMAPPhase.SEARCH)
        try:
            uids = [int(value) for value in raw.split()]
        except ValueError:
            raise IMAPProtocolError(IMAPPhase.SEARCH) from None
        if any(uid < 0 for uid in uids):
            raise IMAPProtocolError(IMAPPhase.SEARCH)
        return sorted(set(uids))

    def _fetch_envelope(
        self, connection: IMAPConnection, *, uidvalidity: int, uid: int
    ) -> MailEnvelope:
        try:
            status, values = connection.uid("FETCH", str(uid), "(RFC822 INTERNALDATE)")
        except IMAPError as error:
            raise _phase_error(error, IMAPPhase.FETCH) from None
        except (
            imaplib.IMAP4.abort,
            imaplib.IMAP4.error,
            OSError,
            ssl.SSLError,
            TimeoutError,
        ) as error:
            raise map_error(error, phase=IMAPPhase.FETCH) from None
        if status != "OK" or len(values) != 1:
            raise IMAPProtocolError(IMAPPhase.FETCH)
        item = values[0]
        if not isinstance(item, tuple) or len(item) != _FETCH_PARTS:
            raise IMAPProtocolError(IMAPPhase.FETCH)
        metadata, raw_message = item
        if not isinstance(metadata, bytes) or not isinstance(raw_message, bytes):
            raise IMAPProtocolError(IMAPPhase.FETCH)
        match = _FETCH_INTERNALDATE_RE.search(metadata)
        internaldate: datetime | None = None
        if match is not None:
            try:
                internaldate = parsedate_to_datetime(match.group(1).decode("ascii"))
            except (UnicodeDecodeError, TypeError, ValueError, IndexError, OverflowError):
                raise IMAPProtocolError(IMAPPhase.FETCH) from None
        try:
            return message_to_envelope(
                raw_message,
                provider_message_id=f"qq-imap-v1:{uidvalidity}:{uid}",
                internaldate=internaldate,
            )
        except IMAPError as error:
            raise _phase_error(error, IMAPPhase.FETCH) from None

    @staticmethod
    def _expect_ok(response: tuple[str, Sequence[bytes | None]], phase: IMAPPhase) -> None:
        if response[0] != "OK":
            if phase is IMAPPhase.LOGIN:
                raise IMAPAuthenticationError(phase)
            raise IMAPProtocolError(phase)


__all__ = [
    "Cursor",
    "CursorClassification",
    "CursorResetReason",
    "IMAPAuthenticationError",
    "IMAPConfigurationError",
    "IMAPCursorError",
    "IMAPError",
    "IMAPPhase",
    "IMAPProtocolError",
    "IMAPTimeoutError",
    "IMAPTransportError",
    "QQIMAPConfig",
    "QQIMAPGateway",
    "classify_cursor",
    "decode_cursor",
    "encode_cursor",
    "message_to_envelope",
]
