"""Durable OpenAI-compatible tool continuation reconstruction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import select

from jobs_status_manager.agent.contracts import PromptToolCall, PromptToolResult
from jobs_status_manager.agent.models import ToolCall, ToolResult
from jobs_status_manager.agent.tools import TOOL_ARGUMENT_MODELS, ToolArguments

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


INCOMPLETE_METADATA_ERROR = "tool continuation metadata is incomplete"
INVALID_METADATA_ERROR = "tool continuation metadata is invalid"


class ToolContinuationStateError(ValueError):
    """Safe failure raised when persisted continuation state cannot be used."""


@dataclass(frozen=True, slots=True)
class LoadedToolState:
    """Validated durable calls, completed results, and pending executions."""

    tool_calls: tuple[PromptToolCall, ...]
    tool_results: tuple[PromptToolResult, ...]
    pending_calls: tuple[tuple[str, str, ToolArguments], ...]
    next_sequence: int
    next_assistant_sequence: int


def load_tool_state(session: Session, run_id: str) -> LoadedToolState:
    """Load one run's provider calls and reject unsafe legacy metadata."""
    calls = tuple(
        session.scalars(
            select(ToolCall).where(ToolCall.agent_run_id == run_id).order_by(ToolCall.sequence)
        )
    )
    results = tuple(
        session.scalars(
            select(ToolResult)
            .join(ToolCall, ToolResult.tool_call_id == ToolCall.id)
            .where(ToolCall.agent_run_id == run_id)
            .order_by(ToolCall.sequence)
        )
    )
    try:
        tool_calls = tuple(
            PromptToolCall.model_validate(
                {
                    "internal_tool_call_id": call.id,
                    "provider_call_id": call.provider_call_id,
                    "provider_type": call.provider_type,
                    "name": call.tool_name,
                    "arguments_json": call.provider_arguments_json,
                    "assistant_sequence": call.assistant_sequence,
                    "sequence": call.sequence,
                }
            )
            for call in calls
        )
    except ValidationError:
        raise ToolContinuationStateError(INCOMPLETE_METADATA_ERROR) from None
    provider_ids = [call.provider_call_id for call in tool_calls]
    sequences = [call.sequence for call in tool_calls]
    assistant_sequences = [call.assistant_sequence for call in tool_calls]
    assistant_batches = list(dict.fromkeys(assistant_sequences))
    if (
        len(set(provider_ids)) != len(provider_ids)
        or sequences != list(range(1, len(tool_calls) + 1))
        or assistant_sequences != sorted(assistant_sequences)
        or assistant_batches != list(range(1, len(assistant_batches) + 1))
    ):
        raise ToolContinuationStateError(INVALID_METADATA_ERROR)
    for call, persisted in zip(tool_calls, calls, strict=True):
        try:
            arguments = json.loads(call.arguments_json)
        except (TypeError, ValueError):
            raise ToolContinuationStateError(INVALID_METADATA_ERROR) from None
        if (
            not isinstance(arguments, dict)
            or arguments != persisted.arguments
            or call.name not in TOOL_ARGUMENT_MODELS
        ):
            raise ToolContinuationStateError(INVALID_METADATA_ERROR)
    failed_result = next((result for result in results if result.error is not None), None)
    if failed_result is not None:
        raise ToolContinuationStateError(failed_result.error)
    result_by_call_id = {result.tool_call_id: result for result in results}
    return LoadedToolState(
        tool_calls=tool_calls,
        tool_results=tuple(
            PromptToolResult(
                internal_tool_call_id=result.tool_call_id,
                data=result.data,
                context_refs=result.context_refs,
            )
            for result in results
        ),
        pending_calls=tuple(
            (call.id, call.tool_name, call.arguments)
            for call in calls
            if call.id not in result_by_call_id
        ),
        next_sequence=len(calls) + 1,
        next_assistant_sequence=max((call.assistant_sequence for call in tool_calls), default=0)
        + 1,
    )
