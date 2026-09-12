"""SQLAlchemy persistence models for conversation state."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from jobs_status_manager.identity.models import Base


class Session(Base):
    """One durable main conversation per user."""

    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("user_id", "session_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_type: Mapped[str] = mapped_column(String(32), nullable=False, default="MAIN")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active_application_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_mail_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_knowledge_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationMessage(Base):
    """Durable user or assistant transcript message."""

    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("provider_event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_msg_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRun(Base):
    """Durable unit of work for one user message."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("user_message_id"),
        Index("ix_agent_runs_retry_scan", "state", "next_retry_at"),
        Index("ix_agent_runs_stale_scan", "state", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    user_message_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_messages.id"), nullable=False
    )
    final_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_messages.id"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolCall(Base):
    """Persisted sequential tool request."""

    __tablename__ = "tool_calls"
    __table_args__ = (UniqueConstraint("agent_run_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[dict[str, str | int | bool | None]] = mapped_column(JSON, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolResult(Base):
    """One durable result for one tool call."""

    __tablename__ = "tool_results"
    __table_args__ = (UniqueConstraint("tool_call_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tool_call_id: Mapped[str] = mapped_column(ForeignKey("tool_calls.id"), nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)
    context_refs: Mapped[dict[str, str | None]] = mapped_column(JSON, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QQInboundIdentity(Base):
    """Unique provider identities for durable QQ ingress idempotency."""

    __tablename__ = "qq_inbound_identities"
    __table_args__ = (UniqueConstraint("identity_type", "identity_value"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("conversation_messages.id"), nullable=False)
    identity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_value: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
