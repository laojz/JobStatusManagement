"""Typed Phase 3 webhook, conversation, and tool contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TOOL_CALLS: Final = 8
MAX_RUN_SECONDS: Final = 120
MAX_TOOL_RESULT_CHARS: Final = 12000
MAX_TOOL_SECONDS: Final = 10
MAX_RECENT_MESSAGES: Final = 20
MAX_PENDING_ACTION_ATTEMPTS: Final = 3
MAX_AGENT_DELIVERY_ATTEMPTS: Final = 3
SYSTEM_PROMPT: Final = (
    "You are a job status assistant. Propose writes; never claim a write before confirmation."
)


@unique
class ReplyMode(StrEnum):
    """Whether a reply targets an inbound message or a user."""

    PASSIVE = "PASSIVE"
    PROACTIVE = "PROACTIVE"


@dataclass(frozen=True, slots=True)
class ReplyTargetError(ValueError):
    """Invalid combination of provider reply-target fields."""

    mode: ReplyMode
    reason: str

    def __str__(self) -> str:
        """Render a safe validation message."""
        return f"invalid {self.mode.value.lower()} reply target: {self.reason}"


@dataclass(frozen=True, slots=True)
class ReplyTarget:
    """Provider-neutral immutable target for a delivery operation."""

    mode: ReplyMode
    provider_name: str
    provider_scope: str
    target_id: str
    message_id: str | None = None
    event_id: str | None = None
    msg_seq: int | None = None

    def __post_init__(self) -> None:
        """Enforce passive and proactive target invariants."""
        if any(
            not identifier.strip()
            for identifier in (self.provider_name, self.provider_scope, self.target_id)
        ):
            raise ReplyTargetError(self.mode, "provider and target identifiers are required")
        if self.mode is ReplyMode.PASSIVE:
            if self.message_id is None or self.event_id is None:
                raise ReplyTargetError(self.mode, "message_id and event_id are required")
            if not self.message_id.strip() or not self.event_id.strip():
                raise ReplyTargetError(self.mode, "message_id and event_id are required")
        if self.mode is ReplyMode.PROACTIVE and any(
            value is not None for value in (self.message_id, self.event_id, self.msg_seq)
        ):
            raise ReplyTargetError(self.mode, "passive message metadata is not allowed")
        if self.msg_seq is not None and self.msg_seq <= 0:
            raise ReplyTargetError(self.mode, "msg_seq must be positive")


@unique
class ProviderErrorKind(StrEnum):
    """How safely a provider failure may be retried."""

    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"
    AMBIGUOUS = "AMBIGUOUS"
    DEFINITE = "DEFINITE"


@dataclass(frozen=True, slots=True)
class ProviderError:
    """Typed provider failure classification for delivery callers."""

    kind: ProviderErrorKind
    provider_name: str
    code: str

    def __str__(self) -> str:
        """Render the provider and machine-readable classification."""
        return f"{self.provider_name} provider error {self.code} ({self.kind.value.lower()})"


class QQInboundEvent(BaseModel):
    """Validated supported QQ text event."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1, max_length=255)
    user_openid: str = Field(min_length=1, max_length=255)
    message_id: str = Field(min_length=1, max_length=255)
    msg_seq: int | None = Field(default=None, gt=0)
    event_type: str = "C2C_MESSAGE_CREATE"
    content: str = Field(default="文件上传", min_length=1, max_length=12000)
    provider_file_id: str | None = Field(default=None, max_length=255)
    filename: str | None = Field(default=None, max_length=255)
    content_type: str | None = Field(default=None, max_length=127)
    size_bytes: int | None = Field(default=None, gt=0)
    file_content_base64: str | None = None
    file_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def require_file_fields(self) -> QQInboundEvent:
        """Require complete metadata for file events only."""
        if self.event_type != "C2C_FILE_CREATE":
            return self
        fields = (
            self.provider_file_id,
            self.filename,
            self.content_type,
            self.size_bytes,
        )
        if any(value is None for value in fields) or (
            self.file_content_base64 is None and self.file_url is None
        ):
            message = "file event requires provider metadata and content"
            raise ValueError(message)
        return self


@unique
class AgentRunState(StrEnum):
    """Durable agent lifecycle states."""

    RUNNING = "RUNNING"
    QUEUED = "QUEUED"
    DELIVERY_PENDING = "DELIVERY_PENDING"
    WAITING_USER_CONFIRMATION = "WAITING_USER_CONFIRMATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@unique
class ToolPermission(StrEnum):
    """Tool capabilities exposed to the agent."""

    READ = "READ"
    TRANSFORM = "TRANSFORM"
    WRITE = "WRITE"


class ToolDefinition(BaseModel):
    """Registry metadata for one tool."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    category: str
    permission: ToolPermission
    requires_confirmation: bool = False
    input_schema: dict[str, str] = Field(default_factory=dict)
    output_schema: dict[str, str] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    """Typed LLM request for one tool invocation."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, str | int | bool | None] = Field(default_factory=dict)


class ConversationResponse(BaseModel):
    """Structured conversation LLM response."""

    model_config = ConfigDict(frozen=True)

    answer: str | None = Field(default=None, max_length=12000)
    tool_call: ToolCallRequest | None = None

    @model_validator(mode="after")
    def require_one_outcome(self) -> ConversationResponse:
        """Require exactly one final answer or ToolCall."""
        if (self.answer is None) == (self.tool_call is None):
            message = "conversation response requires exactly one outcome"
            raise ValueError(message)
        return self


class PromptMessage(BaseModel):
    """One bounded transcript message included in a prompt."""

    model_config = ConfigDict(frozen=True)

    role: str = Field(min_length=1, max_length=16)
    content: str = Field(min_length=1, max_length=12000)


class PromptToolResult(BaseModel):
    """One persisted tool result included with its context references."""

    model_config = ConfigDict(frozen=True)

    data: str = Field(max_length=12000)
    context_refs: dict[str, str | None] = Field(default_factory=dict)


class ConversationPrompt(BaseModel):
    """Bounded prompt sent to the conversation LLM."""

    model_config = ConfigDict(frozen=True)

    system_prompt: str = SYSTEM_PROMPT
    session_summary: str = Field(default="", max_length=12000)
    active_application_id: str | None = None
    active_mail_id: str | None = None
    active_knowledge_document_id: str | None = None
    recent_messages: tuple[PromptMessage, ...] = ()
    user_message: str = Field(min_length=1, max_length=12000)
    tool_results: tuple[PromptToolResult, ...] = ()
