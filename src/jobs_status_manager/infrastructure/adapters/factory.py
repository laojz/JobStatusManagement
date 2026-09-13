"""Production external adapter composition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import httpx2 as httpx
import structlog

from jobs_status_manager.infrastructure.adapters.bailian import BailianEmbedding
from jobs_status_manager.infrastructure.adapters.chroma import LocalChroma
from jobs_status_manager.infrastructure.adapters.openai_compatible_llm import (
    LLMClientOptions,
    OpenAICompatibleLLM,
)
from jobs_status_manager.infrastructure.adapters.qq_imap import QQIMAPConfig, QQIMAPGateway

if TYPE_CHECKING:
    from pydantic import AnyHttpUrl, SecretStr

    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.infrastructure.adapters.protocols import (
        ChromaAdapter,
        EmbeddingAdapter,
        IMAPGateway,
        LLMAdapter,
    )


logger = structlog.get_logger(__name__)


class ClosableResource(Protocol):
    """Resource that can be closed during runtime teardown."""

    def close(self) -> None:
        """Release the resource."""


class AdapterConfigurationError(RuntimeError):
    """A production adapter configuration is incomplete."""

    def __init__(
        self,
        message: str = "embedding_api_key and chroma_path must be configured together",
    ) -> None:
        """Create a safe configuration error without configuration values."""
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """Capabilities supplied by the application and excluded from ownership."""

    imap: IMAPGateway | None = None
    llm: LLMAdapter | None = None
    embedding: EmbeddingAdapter | None = None
    chroma: ChromaAdapter | None = None


class _CloseState:
    __slots__ = ("closed",)

    def __init__(self) -> None:
        self.closed = False


@dataclass(frozen=True, slots=True)
class OwnedAdapters:
    """Optional production adapters and the concrete resources this factory owns."""

    imap: IMAPGateway | None = None
    llm: LLMAdapter | None = None
    embedding: EmbeddingAdapter | None = None
    chroma: ChromaAdapter | None = None
    _owned_resources: tuple[ClosableResource, ...] = ()
    _close_state: _CloseState = field(default_factory=_CloseState, repr=False, compare=False)

    def close(self) -> None:
        """Close owned resources once, retaining the first cleanup failure."""
        if self._close_state.closed:
            return
        self._close_state.closed = True
        cleanup_error: BaseException | None = None
        for resource in reversed(self._owned_resources):
            try:
                resource.close()
            except (OSError, RuntimeError, ValueError) as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None:
            raise cleanup_error

    @property
    def _embedding_client(self) -> BailianEmbedding | None:
        """Preserve the existing CLI/test inspection seam."""
        return self.embedding if isinstance(self.embedding, BailianEmbedding) else None


def _llm_timeout(settings: AppSettings) -> httpx.Timeout:
    return httpx.Timeout(
        connect=settings.llm_connect_timeout_seconds,
        read=settings.llm_read_timeout_seconds,
        write=settings.llm_write_timeout_seconds,
        pool=settings.llm_pool_timeout_seconds,
    )


def _llm_limits(settings: AppSettings) -> httpx.Limits:
    return httpx.Limits(
        max_connections=settings.llm_max_connections,
        max_keepalive_connections=settings.llm_max_keepalive_connections,
    )


def _llm_arguments(settings: AppSettings) -> tuple[AnyHttpUrl, SecretStr]:
    if settings.llm_base_url is None or settings.llm_api_key is None:
        message = "llm_enabled requires complete LLM configuration"
        raise AdapterConfigurationError(message)
    return settings.llm_base_url, settings.llm_api_key


def _close_created(resources: list[ClosableResource]) -> None:
    cleanup_errors: list[BaseException] = []
    for resource in reversed(resources):
        try:
            resource.close()
        except (OSError, RuntimeError, ValueError) as error:
            cleanup_errors.append(error)
    if cleanup_errors:
        logger.warning("adapter_construction_cleanup_failed")


def _construct_adapters(
    settings: AppSettings,
    supplied: AdapterCapabilities,
) -> OwnedAdapters | None:
    imap = supplied.imap
    llm = supplied.llm
    embedding = supplied.embedding
    chroma = supplied.chroma
    should_create_imap = settings.imap_enabled and imap is None
    should_create_llm = settings.llm_enabled and llm is None
    should_create_embedding = (
        embedding is None
        and settings.embedding_api_key is not None
        and settings.chroma_path is not None
    )
    should_create_chroma = (
        chroma is None
        and settings.chroma_path is not None
        and (embedding is not None or settings.embedding_api_key is not None)
    )
    created: list[ClosableResource] = []
    llm_arguments = _llm_arguments(settings) if should_create_llm else None
    try:
        if llm_arguments is not None:
            base_url, api_key = llm_arguments
            llm_adapter = OpenAICompatibleLLM(
                base_url,
                api_key,
                options=LLMClientOptions(
                    timeout=_llm_timeout(settings),
                    limits=_llm_limits(settings),
                ),
            )
            llm = llm_adapter
            created.append(llm_adapter)
        if should_create_imap:
            imap = QQIMAPGateway(QQIMAPConfig.from_settings(settings))
        if should_create_embedding and settings.embedding_api_key is not None:
            embedding = BailianEmbedding(settings.embedding_api_key)
            created.append(embedding)
        if should_create_chroma and settings.chroma_path is not None:
            chroma = LocalChroma(settings.chroma_path)
    except (OSError, RuntimeError, ValueError):
        _close_created(created)
        raise
    owned = OwnedAdapters(imap, llm, embedding, chroma, tuple(created))
    return owned if any((owned.imap, owned.llm, owned.embedding, owned.chroma)) else None


def create_owned_adapters(
    settings: AppSettings,
    supplied: AdapterCapabilities | None = None,
) -> OwnedAdapters | None:
    """Construct missing production adapters while preserving supplied capabilities."""
    supplied_capabilities = supplied if supplied is not None else AdapterCapabilities()
    embedding = supplied_capabilities.embedding
    has_embedding_key = settings.embedding_api_key is not None
    has_chroma_path = settings.chroma_path is not None
    if embedding is None and has_embedding_key and not has_chroma_path:
        raise AdapterConfigurationError
    return _construct_adapters(settings, supplied_capabilities)
