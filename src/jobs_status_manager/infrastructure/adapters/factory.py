"""Production external adapter composition."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jobs_status_manager.infrastructure.adapters.bailian import BailianEmbedding
from jobs_status_manager.infrastructure.adapters.chroma import LocalChroma

if TYPE_CHECKING:
    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.infrastructure.adapters.protocols import (
        ChromaAdapter,
        EmbeddingAdapter,
    )


class AdapterConfigurationError(RuntimeError):
    """Embedding and Chroma settings are incomplete."""

    def __init__(self) -> None:
        """Create a safe pairing error without configuration values."""
        super().__init__("embedding_api_key and chroma_path must be configured together")


@dataclass(frozen=True, slots=True)
class OwnedAdapters:
    """Resources owned by one runtime or CLI invocation."""

    embedding: EmbeddingAdapter
    chroma: ChromaAdapter
    _embedding_client: BailianEmbedding

    def close(self) -> None:
        """Close the owned provider client."""
        self._embedding_client.close()


def create_owned_adapters(settings: AppSettings) -> OwnedAdapters | None:
    """Create production adapters when both settings are present."""
    has_key = settings.embedding_api_key is not None
    has_path = settings.chroma_path is not None
    if not has_key:
        return None
    if not has_path or settings.embedding_api_key is None or settings.chroma_path is None:
        raise AdapterConfigurationError
    embedding = BailianEmbedding(settings.embedding_api_key)
    try:
        chroma = LocalChroma(settings.chroma_path)
    except Exception:
        with suppress(Exception):
            embedding.close()
        raise
    return OwnedAdapters(embedding, chroma, embedding)
