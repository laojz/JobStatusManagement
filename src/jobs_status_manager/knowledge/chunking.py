"""Deterministic text chunking."""

from dataclasses import dataclass
from typing import Final

DEFAULT_CHUNK_CHARS: Final = 1200
DEFAULT_OVERLAP_CHARS: Final = 150


@dataclass(frozen=True, slots=True)
class TextChunk:
    """One deterministic text slice."""

    index: int
    content: str
    section_title: str | None


def chunk_text(
    content: str,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> tuple[TextChunk, ...]:
    """Split normalized text at paragraph boundaries with bounded overlap."""
    normalized = "\n".join(line.rstrip() for line in content.splitlines()).strip()
    if not normalized:
        return ()
    if chunk_chars <= overlap_chars or overlap_chars < 0:
        message = "chunk size must exceed a non-negative overlap"
        raise ValueError(message)
    chunks: list[TextChunk] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_chars, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind("\n", start + overlap_chars, end)
            if boundary > start:
                end = boundary
        text = normalized[start:end].strip()
        if text:
            heading = next(
                (line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")),
                None,
            )
            chunks.append(TextChunk(len(chunks), text, heading or None))
        if end == len(normalized):
            break
        start = max(end - overlap_chars, start + 1)
    return tuple(chunks)
