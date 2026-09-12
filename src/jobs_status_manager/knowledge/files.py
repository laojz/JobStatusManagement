"""Safe upload persistence and text extraction."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Final, Literal, assert_never
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph
from pydantic import TypeAdapter, ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from jobs_status_manager.infrastructure.paths import ensure_private_directory

SUPPORTED_TYPES: Final = {
    ".md": frozenset({"text/markdown", "text/plain", "application/octet-stream"}),
    ".markdown": frozenset({"text/markdown", "text/plain", "application/octet-stream"}),
    ".txt": frozenset({"text/plain", "application/octet-stream"}),
    ".pdf": frozenset({"application/pdf", "application/octet-stream"}),
    ".docx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/octet-stream",
        }
    ),
}
type SupportedExtension = Literal[".md", ".markdown", ".txt", ".pdf", ".docx"]


class UnsupportedUploadError(ValueError):
    """Raised when an upload fails the size or type boundary."""


class DocumentParseError(ValueError):
    """Raised when a supported document cannot produce safe text."""


@dataclass(frozen=True, slots=True)
class UploadMetadata:
    """Provider file metadata validated before download."""

    provider_file_id: str
    filename: str
    content_type: str
    size_bytes: int


def validate_upload(metadata: UploadMetadata, max_bytes: int) -> str:
    """Return the accepted lowercase extension or reject the upload."""
    if Path(metadata.filename).name != metadata.filename:
        message = "filename is invalid"
        raise UnsupportedUploadError(message)
    extension = Path(metadata.filename).suffix.casefold()
    if metadata.size_bytes <= 0 or metadata.size_bytes > max_bytes:
        message = "file size is outside the configured limit"
        raise UnsupportedUploadError(message)
    accepted = SUPPORTED_TYPES.get(extension)
    if accepted is None or metadata.content_type.casefold() not in accepted:
        message = "unsupported file type"
        raise UnsupportedUploadError(message)
    return extension


def validate_upload_content(metadata: UploadMetadata, content: bytes | Path) -> None:
    """Reject bytes that do not match the validated filename and MIME type."""
    try:
        extension = TypeAdapter(SupportedExtension).validate_python(
            Path(metadata.filename).suffix.casefold()
        )
        match content:
            case bytes():
                _validate_content_bytes(extension, content)
            case Path():
                _validate_content_path(extension, content)
            case unreachable:
                assert_never(unreachable)
    except (OSError, UnicodeError, BadZipFile, ValidationError, ValueError) as error:
        message = "file content does not match declared type"
        raise UnsupportedUploadError(message) from error


def _validate_content_bytes(extension: SupportedExtension, content: bytes) -> None:
    match extension:
        case ".md" | ".markdown" | ".txt":
            content.decode("utf-8")
        case ".pdf":
            if not content.startswith(b"%PDF-"):
                message = "PDF header is missing"
                raise ValueError(message)
        case ".docx":
            with ZipFile(BytesIO(content)) as archive:
                _validate_docx_archive(archive)
        case unreachable:
            assert_never(unreachable)


def _validate_content_path(extension: SupportedExtension, path: Path) -> None:
    match extension:
        case ".md" | ".markdown" | ".txt":
            decoder = codecs.getincrementaldecoder("utf-8")()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(64 * 1024), b""):
                    decoder.decode(chunk)
            decoder.decode(b"", final=True)
        case ".pdf":
            with path.open("rb") as source:
                if not source.read(5).startswith(b"%PDF-"):
                    message = "PDF header is missing"
                    raise ValueError(message)
        case ".docx":
            with ZipFile(path) as archive:
                _validate_docx_archive(archive)
        case unreachable:
            assert_never(unreachable)


def _validate_docx_archive(archive: ZipFile) -> None:
    if archive.testzip() is not None:
        message = "DOCX archive contains a corrupt member"
        raise ValueError(message)
    names = frozenset(archive.namelist())
    required = frozenset({"[Content_Types].xml", "word/document.xml"})
    if not required.issubset(names):
        message = "DOCX OOXML parts are missing"
        raise ValueError(message)


def store_upload(data_dir: Path, file_id: str, extension: str, content: bytes) -> Path:
    """Store provider bytes under an application-owned non-user path."""
    directory = ensure_private_directory(data_dir / "uploads")
    path = directory / f"{file_id}{extension}"
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def parse_document(path: Path) -> str:
    """Extract text from a supported stored document."""
    try:
        extension = TypeAdapter(SupportedExtension).validate_python(path.suffix.casefold())
    except ValidationError as error:
        message = "unsupported stored file type"
        raise UnsupportedUploadError(message) from error
    try:
        match extension:
            case ".md" | ".markdown" | ".txt":
                text = path.read_text(encoding="utf-8")
            case ".pdf":
                reader = PdfReader(path, strict=False)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            case ".docx":
                document = Document(str(path))
                blocks: list[str] = []
                for item in document.iter_inner_content():
                    match item:
                        case Table():
                            blocks.extend(
                                "\t".join(cell.text for cell in row.cells) for row in item.rows
                            )
                        case Paragraph():
                            blocks.append(item.text)
                        case unreachable:
                            assert_never(unreachable)
                text = "\n".join(blocks)
            case unreachable:
                assert_never(unreachable)
    except (OSError, UnicodeError, PdfReadError, PackageNotFoundError, ValueError) as error:
        raise DocumentParseError(str(error)) from error
    normalized = text.strip()
    if not normalized:
        message = "document contains no extractable text"
        raise DocumentParseError(message)
    return normalized
