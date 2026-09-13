"""Deterministic external fakes; runtime never constructs these."""

import json
from dataclasses import dataclass, field

from jobs_status_manager.agent.contracts import (
    ConversationPrompt,
    ConversationResponse,
    ProviderError,
    QQInboundEvent,
    ReplyTarget,
)
from jobs_status_manager.knowledge.contracts import VectorHit, VectorRecord
from jobs_status_manager.mail import JobMailAnalysisInput, MailEnvelope, MailPollBatch


class FakeConfigurationError(RuntimeError):
    """Raised when a fake lacks its required configured result."""


@dataclass(frozen=True, slots=True)
class FakeQQDeliveryResult:
    """Deterministic QQ result."""

    success: bool = True
    provider_message_id: str | None = "fake-message"
    provider_error: ProviderError | None = None


@dataclass(frozen=True, slots=True)
class RecordingFake:
    """Base fake recording calls and returning configured outcomes."""

    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    error: Exception | None = None

    def record(self, name: str, *values: str) -> None:
        """Record a call or raise its configured error."""
        if self.error is not None:
            raise self.error
        self.calls.append((name, values))


@dataclass(frozen=True, slots=True)
class FakeLLM(RecordingFake):
    """Deterministic LLM fake."""

    analysis: JobMailAnalysisInput | None = None
    conversation_responses: list[ConversationResponse] = field(default_factory=list)

    def analyze_job_mail(self, prompt: str) -> JobMailAnalysisInput:
        """Record and return a configured typed analysis."""
        self.record("analyze_job_mail", prompt)
        if self.analysis is None:
            raise FakeConfigurationError
        return self.analysis

    def converse(self, prompt: ConversationPrompt) -> ConversationResponse:
        """Record and return the next configured conversation response."""
        self.record("converse", prompt.user_message)
        if not self.conversation_responses:
            raise FakeConfigurationError
        return self.conversation_responses.pop(0)


@dataclass(frozen=True, slots=True)
class FakeQQGateway(RecordingFake):
    """Deterministic QQ fake."""

    delivery: FakeQQDeliveryResult = field(default_factory=FakeQQDeliveryResult)
    inbound_token: str | None = None

    def validate_webhook(self, token: str | None, body: bytes) -> bool:
        """Validate the fake webhook token."""
        self.record("validate_webhook", token or "", body.decode("utf-8"))
        return token == self.inbound_token

    def receive(self, body: bytes) -> QQInboundEvent:
        """Parse a JSON-like event configured by the test body."""
        self.record("receive", body.decode("utf-8"))
        return QQInboundEvent.model_validate(json.loads(body))

    def reply(self, user_id: str, message_id: str, content: str) -> FakeQQDeliveryResult:
        """Record an immediate reply."""
        self.record("reply", user_id, message_id, content)
        return self.delivery

    def push(self, user_id: str, content: str) -> FakeQQDeliveryResult:
        """Record an outbound push."""
        self.record("push", user_id, content)
        return self.delivery

    def deliver(self, target: ReplyTarget, content: str) -> FakeQQDeliveryResult:
        """Record a target-aware local delivery operation."""
        self.record(
            "deliver",
            target.mode.value,
            target.provider_name,
            target.provider_scope,
            target.target_id,
            target.message_id or "",
            target.event_id or "",
            "" if target.msg_seq is None else str(target.msg_seq),
            content,
        )
        return self.delivery

    def send_file(
        self,
        target: ReplyTarget,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> FakeQQDeliveryResult:
        """Record a local file delivery operation."""
        self.record(
            "send_file",
            target.provider_name,
            target.provider_scope,
            target.target_id,
            filename,
            content_type,
            str(len(content)),
        )
        return self.delivery

    def send_image(
        self,
        target: ReplyTarget,
        content_type: str,
        content: bytes,
    ) -> FakeQQDeliveryResult:
        """Record a local image delivery operation."""
        self.record(
            "send_image",
            target.provider_name,
            target.provider_scope,
            target.target_id,
            content_type,
            str(len(content)),
        )
        return self.delivery


@dataclass(frozen=True, slots=True)
class FakeIMAPGateway(RecordingFake):
    """Deterministic IMAP fake."""

    messages: list[MailEnvelope] = field(default_factory=list)
    next_cursor: str = "fake-cursor"
    reset: bool = False
    batch: MailPollBatch | None = None

    def poll(self, account_key: str, cursor: str | None) -> MailPollBatch:
        """Record a poll and return configured messages."""
        self.record("poll", account_key, cursor or "")
        if self.batch is not None:
            return self.batch
        return MailPollBatch(tuple(self.messages), self.next_cursor, self.reset)


@dataclass(frozen=True, slots=True)
class FakeEmbedding(RecordingFake):
    """Deterministic embedding fake."""

    vector: list[float] = field(default_factory=list)

    def embed(self, text: str) -> list[float]:
        """Record an embedding request and return its vector."""
        self.record("embed", text)
        return list(self.vector)


@dataclass(frozen=True, slots=True)
class FakeChroma(RecordingFake):
    """Deterministic Chroma fake."""

    hits: list[VectorHit] = field(default_factory=list)
    records: dict[str, VectorRecord] = field(default_factory=dict)

    def upsert(self, records: tuple[VectorRecord, ...]) -> None:
        """Record idempotent upserts."""
        self.record("upsert", *(record.record_id for record in records))
        self.records.update({record.record_id: record for record in records})

    def query(
        self,
        embedding: tuple[float, ...],
        document_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[VectorHit, ...]:
        """Return configured hits restricted like the real adapter."""
        self.record("query", str(len(embedding)), *document_ids)
        allowed = set(document_ids)
        return tuple(
            hit
            for hit in self.hits
            if hit.record_id in self.records
            and self.records[hit.record_id].metadata.document_id in allowed
        )[:limit]

    def delete(self, record_ids: tuple[str, ...]) -> None:
        """Delete fake records idempotently."""
        self.record("delete", *record_ids)
        for record_id in record_ids:
            self.records.pop(record_id, None)

    def reset(self) -> None:
        """Clear all fake records."""
        self.record("reset")
        self.records.clear()
