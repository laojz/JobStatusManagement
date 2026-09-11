"""Typed contracts for credential-free operations."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from pathlib import Path

MAX_OPERATION_ERROR_CHARS = 2000


@unique
class TaskKind(StrEnum):
    """Durable task families exposed to operators."""

    OUTBOX = "outbox"
    NOTIFICATION = "notification"
    AGENT_RUN = "agent_run"
    PENDING_ACTION = "pending_action"
    KNOWLEDGE_INDEX = "knowledge_index"
    KNOWLEDGE_CLEANUP = "knowledge_cleanup"


@dataclass(frozen=True, slots=True)
class TaskSummary:
    """Redacted durable task state."""

    kind: TaskKind
    task_id: str
    related_id: str | None
    state: str
    attempt_count: int
    last_error: str | None
    next_retry_at: datetime | None
    started_at: datetime | None
    stale: bool


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Result of one controlled task retry."""

    task_id: str
    kind: TaskKind
    state: str
    message: str


@dataclass(frozen=True, slots=True)
class IntegrityResult:
    """SQLite integrity and representative-query result."""

    ok: bool
    integrity: str
    foreign_keys: tuple[str, ...]
    core_tables: tuple[str, ...]
    schema_version: str | None


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Validated SQLite backup result."""

    destination: Path
    integrity: IntegrityResult


class OperationsError(RuntimeError):
    """Safe operator-facing failure."""


class TaskRetryError(OperationsError):
    """A task cannot be retried through the selected operation."""


class DatabaseMaintenanceError(OperationsError):
    """A database maintenance action failed safely."""
