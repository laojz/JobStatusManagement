"""Typed dependencies and results for knowledge operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jobs_status_manager.application_core.domain import PendingActionState
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator


@dataclass(frozen=True, slots=True)
class KnowledgeServices:
    """Dependencies for knowledge transactions."""

    database: Database
    clock: Clock
    ids: IdGenerator


@dataclass(frozen=True, slots=True)
class KnowledgeActionResult:
    """Observable outcome of one confirmed knowledge action."""

    action_id: str
    state: PendingActionState
    document_id: str | None
    message: str
