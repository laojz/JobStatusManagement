"""Knowledge file upload transaction-boundary tests."""

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

import anyio
import pytest
from sqlalchemy import text

from jobs_status_manager.agent import uploads, webhook
from jobs_status_manager.agent.botpy_ingestion import receive_botpy_event
from jobs_status_manager.agent.contracts import QQInboundEvent
from jobs_status_manager.agent.webhook import WebhookContext
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.knowledge.files import UnsupportedUploadError


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


def test_base64_upload_characterization_preserves_bytes_and_private_mode(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    context = _context(database, settings, fake_clock)

    upload = uploads.prepare_upload(context.settings, context.ids, _event())

    assert upload is not None
    path = Path(upload.path)
    assert path.read_bytes() == b"candidate notes"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("notes.pdf", "application/pdf", b"plain text"),
        (
            "notes.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"not a zip archive",
        ),
    ],
)
def test_base64_upload_rejects_mislabeled_binary_content(
    settings: AppSettings,
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    event = _event().model_copy(
        update={
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(content),
            "file_content_base64": base64.b64encode(content).decode("ascii"),
        }
    )

    with pytest.raises(UnsupportedUploadError, match="content"):
        uploads.prepare_upload(settings, _ids(), event)


def test_base64_upload_accepts_pdf_signature(settings: AppSettings) -> None:
    content = b"%PDF-1.7\n"
    event = _event().model_copy(
        update={
            "filename": "notes.pdf",
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
            "file_content_base64": base64.b64encode(content).decode("ascii"),
        }
    )

    upload = uploads.prepare_upload(settings, _ids(), event)

    assert upload is not None
    assert Path(upload.path).read_bytes() == content


def test_base64_upload_accepts_minimal_docx_container(settings: AppSettings) -> None:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    content = output.getvalue()
    event = _event().model_copy(
        update={
            "filename": "notes.docx",
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
            "file_content_base64": base64.b64encode(content).decode("ascii"),
        }
    )

    upload = uploads.prepare_upload(settings, _ids(), event)

    assert upload is not None
    assert Path(upload.path).read_bytes() == content


def test_duplicate_file_delivery_characterization_creates_one_file_and_run(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    context = _context(database, settings, fake_clock)

    first = webhook._persist_event(context, _event())
    duplicate = webhook._persist_event(context, _event())

    assert first.status_code == 202
    assert duplicate.status_code == 200
    with database.engine.connect() as connection:
        file_count = connection.execute(text("SELECT COUNT(*) FROM user_files")).scalar_one()
        run_count = connection.execute(text("SELECT COUNT(*) FROM agent_runs")).scalar_one()
    stored_files = tuple((settings.data_dir / "uploads").iterdir())
    assert file_count == 1
    assert run_count == 1
    assert len(stored_files) == 1


def test_sdk_payload_persists_before_callback_receipt(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    context = _context(database, settings, fake_clock)
    body = json.dumps(
        {
            "op": 0,
            "t": "C2C_MESSAGE_CREATE",
            "d": {
                "event_id": "sdk-event",
                "message_id": "sdk-message",
                "author": {"user_openid": settings.qq_user_openid},
                "content": "sdk message",
            },
        }
    ).encode()

    accepted, status, response_body = anyio.run(receive_botpy_event, body, context)

    assert accepted is True
    assert status == 202
    assert json.loads(response_body) == {"status": "accepted"}


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
