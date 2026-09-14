"""Bounded sequential LLM and Tool Calling loop."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING

import anyio
from pydantic import ValidationError

from jobs_status_manager.agent.contracts import (
    ConversationPrompt,
    ConversationResponse,
    PromptToolResult,
)
from jobs_status_manager.agent.runtime_support import (
    PendingToolCall,
    RunContext,
    execute_tool,
    persist_tool_calls,
    persist_tool_error,
    update_active_context,
)
from jobs_status_manager.agent.tools import WRITE_TOOL_NAMES
from jobs_status_manager.agent.write_runtime import (
    propose_agent_write,
    record_confirmation_delivery,
)
from jobs_status_manager.infrastructure.adapters._openai_compatible_llm_types import LLMError
from jobs_status_manager.infrastructure.safe_errors import safe_external_error

if TYPE_CHECKING:
    from jobs_status_manager.agent.runtime import RuntimeServices
    from jobs_status_manager.infrastructure.database.connection import Database


def _converse(
    services: RuntimeServices,
    prompt: ConversationPrompt,
    timeout_seconds: float,
) -> ConversationResponse:
    async def call() -> ConversationResponse:
        with anyio.fail_after(timeout_seconds):
            return await anyio.to_thread.run_sync(
                services.llm.converse,
                prompt,
                abandon_on_cancel=True,
            )

    return anyio.run(call)


def _next_response(
    services: RuntimeServices,
    context: RunContext,
    tool_results: list[PromptToolResult],
    started: float,
) -> tuple[ConversationResponse | None, str | None, bool]:
    remaining = services.max_run_seconds - (time.monotonic() - started)
    if remaining <= 0:
        return None, "agent run exceeded time limit", False
    try:
        response = _converse(
            services,
            ConversationPrompt(
                system_prompt=context.system_prompt,
                session_summary=context.session_summary,
                active_application_id=context.active_application_id,
                active_mail_id=context.active_mail_id,
                active_knowledge_document_id=context.active_knowledge_document_id,
                recent_messages=context.recent_messages,
                user_message=context.user_message,
                tool_calls=context.tool_calls,
                tool_results=tuple(tool_results),
            ),
            remaining,
        )
    except (RuntimeError, ValueError, TimeoutError) as exception:
        retryable = isinstance(exception, LLMError) and exception.retryable
        return None, safe_external_error(exception), retryable
    return response, None, False


def _with_results(
    database: Database,
    context: RunContext,
    tool_results: list[PromptToolResult],
    pending_tool_calls: tuple[PendingToolCall, ...],
) -> RunContext:
    return update_active_context(
        database,
        replace(
            context,
            tool_results=tuple(tool_results),
            pending_tool_calls=pending_tool_calls,
        ),
    )


def _resume_pending_tools(
    services: RuntimeServices,
    context: RunContext,
    tool_results: list[PromptToolResult],
) -> tuple[RunContext, str | None, bool]:
    pending_calls = context.pending_tool_calls
    for index, pending in enumerate(pending_calls):
        if pending.name in WRITE_TOOL_NAMES:
            try:
                result = propose_agent_write(
                    services,
                    context,
                    pending.internal_tool_call_id,
                    pending.name,
                    pending.arguments,
                )
            except ValidationError:
                error = "malformed arguments for UpdateApplicationStatus"
                persist_tool_error(services, pending.internal_tool_call_id, error)
                return context, error, False
            if result.confirmation_prompt is not None:
                _deliver_confirmation_prompt(services, context, result.confirmation_prompt)
            return context, None, True
        error, result = execute_tool(
            services,
            context,
            pending.internal_tool_call_id,
            pending.name,
            pending.arguments,
        )
        if result is None:
            return context, error, False
        tool_results.append(
            PromptToolResult(
                internal_tool_call_id=pending.internal_tool_call_id,
                data=result.data,
                context_refs=result.context_refs,
            )
        )
        context = _with_results(
            services.database,
            context,
            tool_results,
            pending_calls[index + 1 :],
        )
    return context, None, False


def _deliver_confirmation_prompt(
    services: RuntimeServices,
    context: RunContext,
    prompt: str,
) -> None:
    try:
        target = context.reply_target or services.proactive_target
        delivery = (
            services.qq.push(context.user_id, prompt)
            if target is None
            else services.qq.deliver(target, prompt)
        )
    except (RuntimeError, ValueError, TimeoutError) as exception:
        record_confirmation_delivery(
            services,
            context.run_id,
            success=False,
            error=safe_external_error(exception),
        )
    else:
        record_confirmation_delivery(
            services,
            context.run_id,
            success=delivery.success,
            error=None if delivery.success else "QQ provider rejected delivery",
            provider_error_kind=(
                None
                if delivery.success or delivery.provider_error is None
                else delivery.provider_error.kind
            ),
        )


def run_turns(
    services: RuntimeServices,
    context: RunContext,
) -> tuple[str | None, str | None, bool]:
    """Run a bounded sequence of LLM responses and persisted tool calls."""
    started = time.monotonic()
    context = update_active_context(services.database, context)
    tool_results = list(context.tool_results)
    context, tool_error, suspended = _resume_pending_tools(services, context, tool_results)
    if tool_error is not None:
        return None, tool_error, False
    if suspended:
        return None, None, False
    answer: str | None = None
    error: str | None = None
    retryable = False
    while True:
        response, response_error, response_retryable = _next_response(
            services, context, tool_results, started
        )
        if response is None:
            error = response_error
            retryable = response_retryable
            break
        if response.answer is not None:
            answer = response.answer
            break
        if not response.tool_calls:
            error = "conversation response contained neither answer nor tool calls"
            break
        if len(context.tool_calls) + len(response.tool_calls) > services.max_tool_calls:
            error = "agent run exceeded tool-call limit"
            break
        persisted_calls = persist_tool_calls(services, context, response.tool_calls)
        pending_calls = tuple(
            PendingToolCall(
                internal_tool_call_id=persisted.internal_tool_call_id,
                name=request.name,
                arguments=request.arguments,
            )
            for persisted, request in zip(persisted_calls, response.tool_calls, strict=True)
        )
        context = replace(
            context,
            tool_calls=(*context.tool_calls, *persisted_calls),
            next_sequence=context.next_sequence + len(persisted_calls),
            next_assistant_sequence=context.next_assistant_sequence + 1,
            pending_tool_calls=pending_calls,
        )
        context, tool_error, suspended = _resume_pending_tools(services, context, tool_results)
        if suspended:
            return None, None, False
        if tool_error is not None:
            error = tool_error
            break
    return (
        answer,
        error or (None if answer is not None else "agent run exceeded tool-call limit"),
        retryable if error is not None else False,
    )
