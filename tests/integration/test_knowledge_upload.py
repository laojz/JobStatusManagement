"""Knowledge file upload transaction-boundary tests."""

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest

from jobs_status_manager.agent import uploads, webhook
from jobs_status_manager.agent.contracts import QQInboundEvent
from jobs_status_manager.agent.webhook import WebhookContext
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator


class InjectedPersistenceError(RuntimeError):
    """Failure raised after upload metadata reaches the transaction."""


def _ids() -> DeterministicIdGenerator:
    return DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(1, 30)]
    )


def _event(event_id: str = "event-1") -> QQInboundEvent:
    content = b"candidate notes"
    return QQInboundEvent(
        event_id=event_id,
        user_openid="openid-1",
        message_id=f"message-{event_id}",
        event_type="C2C_FILE_CREATE",
        provider_file_id=f"provider-{event_id}",
        filename="notes.txt",
        content_type="text/plain",
        size_bytes=len(content),
        file_content_base64=base64.b64encode(content).decode("ascii"),
    )


def _context(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> WebhookContext:
    ids = _ids()
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    bootstrap_identity(database, settings, fake_clock, ids)
    return WebhookContext(database, settings, fake_clock, ids)


def test_file_bytes_are_stored_before_the_database_transaction(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(database, settings, fake_clock)
    transaction_active = False
    real_store_upload = uploads.store_upload

    @contextmanager
    def tracked_transaction(value: Database) -> Iterator[object]:
        nonlocal transaction_active
        with transaction(value) as session:
            transaction_active = True
            try:
                yield session
            finally:
                transaction_active = False

    def tracked_store_upload(
        data_dir: Path,
        file_id: str,
        extension: str,
        content: bytes,
    ) -> Path:
        assert transaction_active is False
        return real_store_upload(data_dir, file_id, extension, content)

    monkeypatch.setattr(webhook, "transaction", tracked_transaction)
    monkeypatch.setattr(uploads, "store_upload", tracked_store_upload)

    response = webhook._persist_event(context, _event())

    assert response.status_code == 202


def test_failed_file_metadata_transaction_removes_stored_bytes(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(database, settings, fake_clock)

    @contextmanager
    def failing_transaction(value: Database) -> Iterator[object]:
        with transaction(value) as session:
            yield session
            raise InjectedPersistenceError

    monkeypatch.setattr(webhook, "transaction", failing_transaction)

    with pytest.raises(InjectedPersistenceError):
        webhook._persist_event(context, _event())

    uploads = settings.data_dir / "uploads"
    assert not uploads.exists() or not tuple(uploads.iterdir())
