"""SQLAlchemy persistence models for Phase 2 mail facts and analysis."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from jobs_status_manager.identity.models import Base


class Mail(Base):
    """Immutable provider mail fact with retry metadata."""

    __tablename__ = "mails"
    __table_args__ = (UniqueConstraint("mail_account_id", "provider_message_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    mail_account_id: Mapped[str] = mapped_column(ForeignKey("mail_accounts.id"), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    sender: Mapped[str] = mapped_column(Text, nullable=False)
    recipients: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    processing_state: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobMailAnalysis(Base):
    """Current effective validated analysis, one row per mail."""

    __tablename__ = "job_mail_analyses"
    __table_args__ = (UniqueConstraint("mail_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mail_id: Mapped[str] = mapped_column(ForeignKey("mails.id"), nullable=False)
    mail_type: Mapped[str] = mapped_column(String(32), nullable=False)
    application: Mapped[dict[str, str | None]] = mapped_column(JSON, nullable=False)
    status_suggestion: Mapped[dict[str, str | int | bool | None]] = mapped_column(
        JSON, nullable=False
    )
    details: Mapped[dict[str, str | list[str] | None]] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessedEvent(Base):
    """Consumer idempotency marker."""

    __tablename__ = "processed_events"
    __table_args__ = (UniqueConstraint("consumer_name", "event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    consumer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
