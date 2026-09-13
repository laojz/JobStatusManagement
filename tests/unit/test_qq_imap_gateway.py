import imaplib
from email.message import EmailMessage

import pytest
from pydantic import SecretStr

from jobs_status_manager.infrastructure.adapters.qq_imap import (
    ConnectionOptions,
    Cursor,
    IMAPAuthenticationError,
    IMAPProtocolError,
    IMAPTimeoutError,
    QQIMAPConfig,
    QQIMAPGateway,
    encode_cursor,
)

CallArgument = str | tuple[str | bool | None, ...]


class FakeConnection:
    def __init__(self, messages: dict[int, bytes], *, failure: BaseException | None = None) -> None:
        self.messages = messages
        self.failure = failure
        self.calls: list[tuple[str, CallArgument]] = []
        self.closed = False

    def login(self, user: str, password: str) -> tuple[str, list[bytes | None]]:
        self.calls.append(("login", (user, password)))
        if self.failure is not None:
            raise self.failure
        return "OK", [b"logged in"]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes | None]]:
        self.calls.append(("select", (mailbox, readonly)))
        return "OK", [b"3"]

    def response(self, code: str) -> tuple[str | None, list[bytes] | None]:
        self.calls.append(("response", code))
        return "UIDVALIDITY", [b"7"]

    def uid(self, command: str, *args: str) -> tuple[str, list[bytes | tuple[bytes, bytes] | None]]:
        self.calls.append((command, args))
        if command == "SEARCH":
            query = args[-1]
            if not isinstance(query, str):
                raise AssertionError(query)
            if query == "ALL":
                value = " ".join(str(uid) for uid in self.messages)
            else:
                start = int(query.removeprefix("UID ").split(":", 1)[0])
                value = " ".join(str(uid) for uid in self.messages if uid >= start)
            return "OK", [value.encode()]
        if command == "FETCH":
            uid = int(args[0])
            metadata = f'{uid} (INTERNALDATE "10-Sep-2024 12:00:00 +0000" RFC822)'.encode()
            return "OK", [(metadata, self.messages[uid])]
        raise AssertionError(command)

    def logout(self) -> tuple[str, list[bytes | None]]:
        self.calls.append(("logout", ()))
        self.closed = True
        return "BYE", [b"closed"]

    def shutdown(self) -> None:
        self.calls.append(("shutdown", ()))


def _config(batch_size: int = 2) -> QQIMAPConfig:
    return QQIMAPConfig(
        account="account@example.com",
        auth_code=SecretStr("auth-code"),
        batch_size=batch_size,
        connect_timeout_seconds=3,
        command_timeout_seconds=4,
    )


def _message(subject: str) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "sender@example.com"
    message["Date"] = "Tue, 10 Sep 2024 12:00:00 +0000"
    message.set_content(subject)
    return message.as_bytes()


def test_poll_establishes_initial_baseline_without_returning_history() -> None:
    connections: list[FakeConnection] = []

    def factory(_options: ConnectionOptions) -> FakeConnection:
        connection = FakeConnection({4: _message("old")})
        connections.append(connection)
        return connection

    gateway = QQIMAPGateway(_config(), connection_factory=factory)

    result = gateway.poll("internal-account", None)

    assert result.envelopes == ()
    assert result.next_cursor == encode_cursor(Cursor(uidvalidity=7, uid=4))
    assert result.reset is True
    assert connections[0].calls[:4] == [
        ("login", ("account@example.com", "auth-code")),
        ("select", ("INBOX", True)),
        ("response", "UIDVALIDITY"),
        ("SEARCH", ("ALL",)),
    ]
    assert connections[0].closed is True


def test_poll_fetches_uid_range_in_order_and_bounds_batch() -> None:
    connection = FakeConnection({3: _message("three"), 1: _message("one"), 2: _message("two")})
    gateway = QQIMAPGateway(_config(batch_size=2), connection_factory=lambda _options: connection)

    result = gateway.poll("internal-account", encode_cursor(Cursor(uidvalidity=7, uid=0)))

    assert [envelope.subject for envelope in result.envelopes] == ["one", "two"]
    assert [envelope.provider_message_id for envelope in result.envelopes] == [
        "qq-imap-v1:7:1",
        "qq-imap-v1:7:2",
    ]
    assert result.next_cursor == encode_cursor(Cursor(uidvalidity=7, uid=2))
    assert result.reset is False
    assert ("SEARCH", ("UID 1:*",)) in connection.calls
    assert ("FETCH", ("1", "(RFC822 INTERNALDATE)")) in connection.calls


def test_each_poll_receives_a_fresh_default_ssl_context() -> None:
    options: list[ConnectionOptions] = []

    def factory(connection_options: ConnectionOptions) -> FakeConnection:
        options.append(connection_options)
        return FakeConnection({})

    gateway = QQIMAPGateway(_config(), connection_factory=factory)

    gateway.poll("internal-account", None)
    gateway.poll("internal-account", encode_cursor(Cursor(uidvalidity=7, uid=0)))

    assert len(options) == 2
    assert options[0].ssl_context is not options[1].ssl_context
    assert (options[0].host, options[0].port) == ("imap.qq.com", 993)
    assert (options[0].connect_timeout_seconds, options[0].command_timeout_seconds) == (3, 4)


def test_poll_maps_login_rejection_without_exposing_secret() -> None:
    connection = FakeConnection({}, failure=imaplib.IMAP4.error("NO auth-code"))
    gateway = QQIMAPGateway(_config(), connection_factory=lambda _options: connection)

    with pytest.raises(IMAPAuthenticationError) as caught:
        gateway.poll("internal-account", None)

    assert "auth-code" not in str(caught.value)
    assert connection.closed is True


def test_poll_preserves_fetch_error_over_logout_error() -> None:
    class FailingConnection(FakeConnection):
        def uid(
            self, command: str, *args: str
        ) -> tuple[str, list[bytes | tuple[bytes, bytes] | None]]:
            if command == "FETCH":
                raise IMAPProtocolError
            return super().uid(command, *args)

        def logout(self) -> tuple[str, list[bytes | None]]:
            error_message = "logout failure"
            raise imaplib.IMAP4.error(error_message)

    connection = FailingConnection({1: _message("one")})
    gateway = QQIMAPGateway(_config(), connection_factory=lambda _options: connection)

    with pytest.raises(IMAPProtocolError):
        gateway.poll("internal-account", encode_cursor(Cursor(uidvalidity=7, uid=0)))


@pytest.mark.parametrize("failure", [TimeoutError(), TimeoutError("connect")])
def test_poll_maps_timeout_failures(failure: BaseException) -> None:
    connection = FakeConnection({}, failure=failure)
    gateway = QQIMAPGateway(_config(), connection_factory=lambda _options: connection)

    with pytest.raises(IMAPTimeoutError):
        gateway.poll("internal-account", None)
