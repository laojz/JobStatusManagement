import errno
import imaplib
import ssl
from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from jobs_status_manager.infrastructure.adapters._qq_imap_connection import cleanup_connection
from jobs_status_manager.infrastructure.adapters.qq_imap import (
    ConnectionOptions,
    Cursor,
    IMAPAuthenticationError,
    IMAPConfigurationError,
    IMAPCursorError,
    IMAPError,
    IMAPPhase,
    IMAPProtocolError,
    IMAPTimeoutError,
    IMAPTransportError,
    QQIMAPConfig,
    QQIMAPGateway,
    encode_cursor,
)

type IMAPFailure = imaplib.IMAP4.error | OSError | ssl.SSLError | TimeoutError


class SubclassedOSError(OSError):
    pass


class DiagnosticConnection:
    def __init__(
        self,
        *,
        failure_phase: IMAPPhase | None = None,
        failure: IMAPFailure | None = None,
    ) -> None:
        self._failure_phase = failure_phase
        self._failure = failure

    def _raise_at(self, phase: IMAPPhase) -> None:
        if self._failure_phase is phase and self._failure is not None:
            raise self._failure

    def login(self, user: str, password: str) -> tuple[str, Sequence[bytes | None]]:
        del user, password
        self._raise_at(IMAPPhase.LOGIN)
        return "OK", [b"logged in"]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, Sequence[bytes | None]]:
        del mailbox
        assert readonly is True
        self._raise_at(IMAPPhase.SELECT)
        return "OK", [b"1"]

    def response(self, code: str) -> tuple[str | None, list[bytes] | None]:
        del code
        self._raise_at(IMAPPhase.UIDVALIDITY)
        return "UIDVALIDITY", [b"7"]

    def uid(
        self, command: str, *args: str
    ) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]:
        if command == "SEARCH":
            self._raise_at(IMAPPhase.SEARCH)
            return "OK", [b"1"]
        if command == "FETCH":
            self._raise_at(IMAPPhase.FETCH)
            metadata = b'1 (INTERNALDATE "10-Sep-2024 12:00:00 +0000")'
            message = b"Date: Tue, 10 Sep 2024 12:00:00 +0000\n\nbody"
            return "OK", [(metadata, message)]
        raise AssertionError(command)

    def logout(self) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]:
        self._raise_at(IMAPPhase.CLEANUP)
        return "BYE", [b"closed"]

    def shutdown(self) -> None:
        return None


class CleanupConnection(DiagnosticConnection):
    def __init__(
        self,
        *,
        logout_failure: IMAPFailure | None = None,
        shutdown_failure: IMAPFailure | None = None,
        logout_status: str = "BYE",
    ) -> None:
        super().__init__()
        self.logout_failure = logout_failure
        self.shutdown_failure = shutdown_failure
        self.logout_status = logout_status
        self.calls: list[str] = []

    def logout(self) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]:
        self.calls.append("logout")
        if self.logout_failure is not None:
            raise self.logout_failure
        return self.logout_status, [b"closed"]

    def shutdown(self) -> None:
        self.calls.append("shutdown")
        if self.shutdown_failure is not None:
            raise self.shutdown_failure


class PrimaryAndShutdownFailure(CleanupConnection):
    def uid(
        self, command: str, *args: str
    ) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]:
        if command == "FETCH":
            error_message = "primary secret"
            raise TimeoutError(error_message)
        return super().uid(command, *args)


def _config() -> QQIMAPConfig:
    return QQIMAPConfig(
        account="account@example.com",
        auth_code=SecretStr("auth-code"),
        batch_size=1,
    )


def _poll(connection: DiagnosticConnection) -> None:
    gateway = QQIMAPGateway(_config(), connection_factory=lambda _options: connection)
    gateway.poll("internal-account", encode_cursor(Cursor(uidvalidity=7, uid=0)))


@pytest.mark.parametrize(
    "error_type",
    [
        IMAPError,
        IMAPCursorError,
        IMAPProtocolError,
        IMAPConfigurationError,
        IMAPAuthenticationError,
        IMAPTransportError,
        IMAPTimeoutError,
    ],
)
def test_unphased_imap_errors_preserve_class_only_strings(
    error_type: type[IMAPError],
) -> None:
    error = error_type()

    expected = "IMAP cursor is invalid" if error_type is IMAPCursorError else error_type.__name__
    assert str(error) == expected


def test_phased_error_string_contains_only_class_and_allowlisted_phase() -> None:
    error = IMAPTransportError(IMAPPhase.CONNECT)

    assert error.phase is IMAPPhase.CONNECT
    assert str(error) == "IMAPTransportError: connect"


@pytest.mark.parametrize(
    ("failure", "expected_type"),
    [
        (TimeoutError("secret timeout detail"), IMAPTimeoutError),
        (OSError("secret transport detail"), IMAPTransportError),
        (ssl.SSLError("secret TLS detail"), IMAPTransportError),
        (imaplib.IMAP4.abort("secret abort detail"), IMAPTransportError),
    ],
)
def test_connect_failures_are_safely_classified(
    failure: IMAPFailure,
    expected_type: type[IMAPError],
) -> None:
    def factory(_options: ConnectionOptions) -> DiagnosticConnection:
        raise failure

    gateway = QQIMAPGateway(_config(), connection_factory=factory)

    with pytest.raises(expected_type) as caught:
        gateway.poll("internal-account", None)

    assert caught.value.phase is IMAPPhase.CONNECT
    assert str(caught.value) == f"{expected_type.__name__}: connect"


@pytest.mark.parametrize(
    ("phase", "failure", "expected_type"),
    [
        (
            IMAPPhase.LOGIN,
            imaplib.IMAP4.error("NO account@example.com auth-code"),
            IMAPAuthenticationError,
        ),
        (IMAPPhase.SELECT, imaplib.IMAP4.error("select secret"), IMAPProtocolError),
        (IMAPPhase.UIDVALIDITY, imaplib.IMAP4.error("uidvalidity secret"), IMAPProtocolError),
        (IMAPPhase.SEARCH, TimeoutError("search secret"), IMAPTimeoutError),
        (IMAPPhase.FETCH, OSError("header and body secret"), IMAPTransportError),
    ],
)
def test_command_failures_report_the_actual_phase_without_low_level_text(
    phase: IMAPPhase,
    failure: IMAPFailure,
    expected_type: type[IMAPError],
) -> None:
    connection = DiagnosticConnection(failure_phase=phase, failure=failure)

    with pytest.raises(expected_type) as caught:
        _poll(connection)

    assert caught.value.phase is phase
    assert str(caught.value) == f"{expected_type.__name__}: {phase.value}"


def test_cleanup_failure_reports_cleanup_phase_when_poll_succeeds() -> None:
    connection = DiagnosticConnection(
        failure_phase=IMAPPhase.CLEANUP,
        failure=imaplib.IMAP4.error("logout secret"),
    )

    with pytest.raises(IMAPProtocolError) as caught:
        _poll(connection)

    assert caught.value.phase is IMAPPhase.CLEANUP
    assert str(caught.value) == "IMAPProtocolError: cleanup"


def test_cleanup_failure_never_masks_primary_fetch_error() -> None:
    class PrimaryAndCleanupFailure(DiagnosticConnection):
        def uid(
            self, command: str, *args: str
        ) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]:
            if command == "FETCH":
                error_message = "primary body secret"
                raise TimeoutError(error_message)
            return super().uid(command, *args)

        def logout(self) -> tuple[str, Sequence[bytes | tuple[bytes, bytes] | None]]:
            error_message = "cleanup secret"
            raise imaplib.IMAP4.error(error_message)

    with pytest.raises(IMAPTimeoutError) as caught:
        _poll(PrimaryAndCleanupFailure())

    assert caught.value.phase is IMAPPhase.FETCH
    assert str(caught.value) == "IMAPTimeoutError: fetch"


@pytest.mark.parametrize("logout_status", ["BYE", "OK"])
def test_cleanup_ignores_plain_ebadf_from_shutdown_after_acceptable_logout(
    logout_status: str,
) -> None:
    connection = CleanupConnection(
        logout_status=logout_status,
        shutdown_failure=OSError(errno.EBADF, "shutdown secret"),
    )

    error = cleanup_connection(connection)

    assert error is None
    assert connection.calls == ["logout", "shutdown"]


@pytest.mark.parametrize(
    "shutdown_failure",
    [
        OSError(errno.EIO, "transport secret"),
        OSError("missing errno secret"),
        SubclassedOSError(errno.EBADF, "subclass secret"),
        ssl.SSLError("TLS secret"),
        TimeoutError("timeout secret"),
    ],
)
def test_cleanup_preserves_non_ebadf_shutdown_errors(shutdown_failure: IMAPFailure) -> None:
    connection = CleanupConnection(shutdown_failure=shutdown_failure)

    error = cleanup_connection(connection)

    expected_type = (
        IMAPTimeoutError if isinstance(shutdown_failure, TimeoutError) else IMAPTransportError
    )
    assert isinstance(error, expected_type)
    assert error.phase is IMAPPhase.CLEANUP
    assert str(error) == f"{type(error).__name__}: cleanup"


def test_cleanup_logout_ebadf_remains_an_error_and_shutdown_still_runs() -> None:
    connection = CleanupConnection(logout_failure=OSError(errno.EBADF, "logout secret"))

    error = cleanup_connection(connection)

    assert isinstance(error, IMAPTransportError)
    assert error.phase is IMAPPhase.CLEANUP
    assert str(error) == "IMAPTransportError: cleanup"
    assert connection.calls == ["logout", "shutdown"]
    assert "logout secret" not in str(error)


def test_cleanup_shutdown_ebadf_does_not_mask_primary_poll_error() -> None:
    connection = PrimaryAndShutdownFailure(
        shutdown_failure=OSError(errno.EBADF, "shutdown secret"),
    )

    with pytest.raises(IMAPTimeoutError) as caught:
        _poll(connection)

    assert caught.value.phase is IMAPPhase.FETCH
    assert str(caught.value) == "IMAPTimeoutError: fetch"
    assert connection.calls == ["logout", "shutdown"]
