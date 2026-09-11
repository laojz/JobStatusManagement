"""QQ file upload preparation outside relational transactions."""

import base64
from dataclasses import dataclass
from pathlib import Path

from jobs_status_manager.agent.contracts import QQInboundEvent
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.ids import IdGenerator
from jobs_status_manager.knowledge.files import (
    UnsupportedUploadError,
    UploadMetadata,
    store_upload,
    validate_upload,
)


@dataclass(frozen=True, slots=True)
class StoredUpload:
    """Validated file bytes awaiting relational metadata persistence."""

    file_id: str
    metadata: UploadMetadata
    path: str


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
        or event.file_content_base64 is None
    ):
        message = "file event metadata is incomplete"
        raise UnsupportedUploadError(message)
    metadata = UploadMetadata(
        event.provider_file_id,
        event.filename,
        event.content_type,
        event.size_bytes,
    )
    extension = validate_upload(metadata, settings.max_upload_bytes)
    content = base64.b64decode(event.file_content_base64, validate=True)
    if len(content) != event.size_bytes:
        message = "file size does not match provider metadata"
        raise UnsupportedUploadError(message)
    file_id = str(ids.new_id())
    path = store_upload(settings.data_dir, file_id, extension, content)
    return StoredUpload(file_id, metadata, str(path))


def discard_upload(upload: StoredUpload) -> None:
    """Remove bytes whose relational persistence did not commit."""
    Path(upload.path).unlink(missing_ok=True)
