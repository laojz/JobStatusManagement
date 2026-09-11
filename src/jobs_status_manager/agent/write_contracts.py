"""Typed contracts for confirmation-gated Agent writes."""

from dataclasses import dataclass

from pydantic import ConfigDict

from jobs_status_manager.application_core.domain import StatusUpdateArguments


class UpdateApplicationStatusArguments(StatusUpdateArguments):
    """Boundary schema for the status write Tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """Immediate Tool result or confirmation-gated proposal."""

    data: str
    context_refs: dict[str, str | None]
    pending_action_id: str | None = None
    confirmation_prompt: str | None = None
