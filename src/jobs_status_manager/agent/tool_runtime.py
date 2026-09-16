"""Durable execution boundary for Agent tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
from sqlalchemy import select

from jobs_status_manager.agent.contracts import AgentRunState, PromptToolCall, ToolCallRequest
from jobs_status_manager.agent.models import AgentRun, ConversationMessage, ToolCall, ToolResult
from jobs_status_manager.agent.write_preflight import recovery_result_data
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.knowledge.index import IndexServices
from jobs_status_manager.knowledge.tooling import execute_knowledge

if TYPE_CHECKING:
    from jobs_status_manager.agent.runtime import RuntimeServices
    from jobs_status_manager.agent.runtime_support import PendingToolCall, RunContext
    from jobs_status_manager.agent.tools import ToolArguments
    from jobs_status_manager.agent.write_contracts import ToolExecution
    from jobs_status_manager.agent.write_preflight import MalformedWrite


@dataclass(frozen=True, slots=True)
class WriteRecoveryRequest:
    """Typed inputs for one malformed pending-batch recovery."""

    context: RunContext
    pending_calls: tuple[PendingToolCall, ...]
    malformed: MalformedWrite


def persist_tool_calls(
    services: RuntimeServices,
    context: RunContext,
    requests: tuple[ToolCallRequest, ...],
) -> tuple[PromptToolCall, ...]:
    """Persist a complete provider assistant tool-call batch before execution."""
    persisted: list[PromptToolCall] = []
    with transaction(services.database) as session:
        for offset, request in enumerate(requests):
            call_id = str(services.ids.new_id())
            sequence = context.next_sequence + offset
            session.add(
                ToolCall(
                    id=call_id,
                    agent_run_id=context.run_id,
                    tool_name=request.name,
                    arguments=request.arguments,
                    provider_call_id=request.provider_call_id,
                    provider_type=request.provider_type,
                    provider_arguments_json=request.arguments_json,
                    assistant_sequence=context.next_assistant_sequence,
                    sequence=sequence,
                    created_at=services.clock.now(),
                )
            )
            persisted.append(
                PromptToolCall(
                    internal_tool_call_id=call_id,
                    provider_call_id=request.provider_call_id,
                    provider_type=request.provider_type,
                    name=request.name,
                    arguments_json=request.arguments_json,
                    assistant_sequence=context.next_assistant_sequence,
                    sequence=sequence,
                )
            )
    return tuple(persisted)


def persist_write_recovery(
    services: RuntimeServices,
    request: WriteRecoveryRequest,
) -> str:
    """Atomically persist handled batch results and one durable clarification."""
    now = services.clock.now()
    call_ids = tuple(pending.internal_tool_call_id for pending in request.pending_calls)
    with transaction(services.database) as session:
        persisted_call_ids = set(
            session.scalars(
                select(ToolResult.tool_call_id).where(ToolResult.tool_call_id.in_(call_ids))
            )
        )
        for pending in request.pending_calls:
            if pending.internal_tool_call_id in persisted_call_ids:
                continue
            session.add(
                ToolResult(
                    id=str(services.ids.new_id()),
                    tool_call_id=pending.internal_tool_call_id,
                    data=recovery_result_data(
                        pending.internal_tool_call_id,
                        pending.name,
                        request.malformed,
                    ),
                    context_refs={},
                    error=None,
                    started_at=now,
                    completed_at=now,
                    created_at=now,
                )
            )
        run = session.get(AgentRun, request.context.run_id)
        if run is None:
            return request.malformed.clarification
        answer = request.malformed.clarification
        if run.final_message_id is None:
            message_id = str(services.ids.new_id())
            session.add(
                ConversationMessage(
                    id=message_id,
                    session_id=request.context.session_id,
                    role="assistant",
                    content=answer,
                    provider_event_id=None,
                    created_at=now,
                )
            )
            session.flush()
            run.final_message_id = message_id
        else:
            message = session.get(ConversationMessage, run.final_message_id)
            if message is not None:
                answer = message.content
        run.state = AgentRunState.DELIVERY_PENDING.value
        run.error = None
        run.delivery_state = None
        run.delivery_error = None
        run.provider_message_id = None
        run.next_retry_at = None
        run.completed_at = now
        return answer


async def _execute_tool_async(
    services: RuntimeServices,
    context: RunContext,
    name: str,
    arguments: ToolArguments,
) -> ToolExecution:
    with anyio.fail_after(services.tool_timeout_seconds):
        return await anyio.to_thread.run_sync(
            services.tool_executor,
            services.database,
            context.user_id,
            name,
            arguments,
            abandon_on_cancel=True,
        )


def execute_tool(
    services: RuntimeServices,
    context: RunContext,
    call_id: str,
    name: str,
    arguments: ToolArguments,
) -> tuple[str, ToolExecution | None]:
    """Execute a tool outside transactions and persist exactly one result."""
    started = services.clock.now()
    try:
        if (
            name == "SearchKnowledge"
            and services.embedding is not None
            and services.chroma is not None
        ):
            result = execute_knowledge(
                IndexServices(
                    services.database,
                    services.clock,
                    services.embedding,
                    services.chroma,
                ),
                context.user_id,
                arguments,
            )
        else:
            result = anyio.run(_execute_tool_async, services, context, name, arguments)
    except TimeoutError:
        error = "tool execution exceeded time limit"
        result = None
    except (PermissionError, RuntimeError, ValueError, LookupError) as exception:
        error = safe_external_error(exception)
        result = None
    if result is not None and len(result.data) > services.max_result_chars:
        error = "tool result exceeded size limit"
        result = None
    with transaction(services.database) as session:
        session.add(
            ToolResult(
                id=str(services.ids.new_id()),
                tool_call_id=call_id,
                data="" if result is None else result.data,
                context_refs={} if result is None else result.context_refs,
                error=error if result is None else None,
                started_at=started,
                completed_at=services.clock.now(),
                created_at=services.clock.now(),
            )
        )
    return error if result is None else "", result


def persist_tool_error(
    services: RuntimeServices,
    call_id: str,
    error: str,
) -> None:
    """Persist a boundary validation failure for one ToolCall."""
    now = services.clock.now()
    with transaction(services.database) as session:
        session.add(
            ToolResult(
                id=str(services.ids.new_id()),
                tool_call_id=call_id,
                data="",
                context_refs={},
                error=error,
                started_at=now,
                completed_at=now,
                created_at=now,
            )
        )
