"""Typed external adapter protocols."""

from typing import Protocol

from jobs_status_manager.agent.contracts import (
    ConversationPrompt,
    ConversationResponse,
    ProviderError,
    QQInboundEvent,
    ReplyTarget,
)
from jobs_status_manager.knowledge.contracts import VectorHit, VectorRecord
from jobs_status_manager.mail import JobMailAnalysisInput, MailEnvelope


class LLMAdapter(Protocol):
    """Language model boundary."""

    def complete(self, prompt: str) -> str:
        """Complete a prompt for legacy callers."""
        ...

    def analyze_job_mail(self, prompt: str) -> JobMailAnalysisInput:
        """Return a validated job-mail analysis."""
        ...

    def converse(self, prompt: ConversationPrompt) -> ConversationResponse:
        """Return one typed final answer or one typed tool call."""
        ...


class QQDeliveryResult(Protocol):
    """Provider delivery result contract."""

    @property
    def success(self) -> bool:
        """Whether the provider accepted the message."""
        ...

    @property
    def provider_message_id(self) -> str | None:
        """Provider trace identifier."""
        ...

    @property
    def provider_error(self) -> ProviderError | None:
        """Classified provider failure, when one was returned."""
        ...


class QQGateway(Protocol):
    """QQ outbound push boundary."""

    def push(self, user_id: str, content: str) -> QQDeliveryResult:
        """Push content to a user."""
        ...

    def validate_webhook(self, token: str | None, body: bytes) -> bool:
        """Validate the configured webhook boundary."""
        ...

    def receive(self, body: bytes) -> QQInboundEvent:
        """Parse a supported inbound QQ event."""
        ...

    def reply(self, user_id: str, message_id: str, content: str) -> QQDeliveryResult:
        """Reply to the current inbound message."""
        ...

    def deliver(self, target: ReplyTarget, content: str) -> QQDeliveryResult:
        """Deliver content using the target's explicit passive or proactive mode."""
        ...

    def send_file(
        self,
        target: ReplyTarget,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> QQDeliveryResult:
        """Describe a file delivery operation without selecting a provider SDK."""
        ...

    def send_image(
        self, target: ReplyTarget, content_type: str, content: bytes
    ) -> QQDeliveryResult:
        """Describe an image delivery operation without selecting a provider SDK."""
        ...


class IMAPGateway(Protocol):
    """IMAP polling boundary."""

    def poll(self, account_key: str, cursor: str | None) -> list[MailEnvelope]:
        """Poll an account after its persisted high-watermark."""
        ...


class EmbeddingAdapter(Protocol):
    """Embedding boundary reserved for a later phase."""

    def embed(self, text: str) -> list[float]:
        """Create an embedding vector."""
        ...


class ChromaAdapter(Protocol):
    """Rebuildable vector-index boundary."""

    def upsert(self, records: tuple[VectorRecord, ...]) -> None:
        """Upsert stable chunk records."""
        ...

    def query(
        self,
        embedding: tuple[float, ...],
        document_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[VectorHit, ...]:
        """Return nearest hits restricted to eligible documents."""
        ...

    def delete(self, record_ids: tuple[str, ...]) -> None:
        """Delete stable chunk records idempotently."""
        ...

    def reset(self) -> None:
        """Clear the single rebuildable collection."""
        ...
