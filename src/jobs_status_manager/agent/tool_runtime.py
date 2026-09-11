"""Durable execution boundary for Agent tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio

from jobs_status_manager.agent.models import ToolCall, ToolResult
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.knowledge.index import IndexServices
from jobs_status_manager.knowledge.tooling import execute_knowledge

if TYPE_CHECKING:
    from jobs_status_manager.agent.runtime import RuntimeServices
    from jobs_status_manager.agent.runtime_support import RunContext
    from jobs_status_manager.agent.tools import ToolArguments
    from jobs_status_manager.agent.write_contracts import ToolExecution


def persist_tool_call(
    services: RuntimeServices,
    context: RunContext,
    sequence: int,
    name: str,
    arguments: ToolArguments,
) -> str:
    """Persist a tool request before executing its external query."""
    call_id = str(services.ids.new_id())
    with transaction(services.database) as session:
        session.add(
            ToolCall(
                id=call_id,
                agent_run_id=context.run_id,
                tool_name=name,
                arguments=arguments,
                sequence=sequence,
                created_at=services.clock.now(),
            )
        )
    return call_id


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
