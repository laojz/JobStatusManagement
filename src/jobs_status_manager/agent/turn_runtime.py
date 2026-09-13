"""Bounded sequential LLM and Tool Calling loop."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import anyio
from pydantic import ValidationError

from jobs_status_manager.agent.contracts import (
    ConversationPrompt,
    ConversationResponse,
    PromptToolResult,
)
from jobs_status_manager.agent.runtime_support import (
    RunContext,
    execute_tool,
    persist_tool_call,
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
    from jobs_status_manager.agent.tools import ToolArguments
    from jobs_status_manager.agent.write_contracts import ToolExecution
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
) -> RunContext:
    return update_active_context(
        database,
        RunContext(
            run_id=context.run_id,
            session_id=context.session_id,
            user_id=context.user_id,
            system_prompt=context.system_prompt,
            session_summary=context.session_summary,
            active_application_id=context.active_application_id,
            active_mail_id=context.active_mail_id,
            active_knowledge_document_id=context.active_knowledge_document_id,
            recent_messages=context.recent_messages,
            user_message=context.user_message,
            tool_results=tuple(tool_results),
            next_sequence=context.next_sequence,
            pending_tool_call_id=None,
            pending_tool_name=None,
            pending_tool_arguments=None,
            final_message_id=context.final_message_id,
            final_message_content=context.final_message_content,
            state=context.state,
            reply_target=context.reply_target,
        ),
    )


def _resume_pending_tool(
    services: RuntimeServices,
    context: RunContext,
    tool_results: list[PromptToolResult],
) -> tuple[RunContext, str | None] | None:
    if context.pending_tool_call_id is None:
        return None
    if context.pending_tool_name is None or context.pending_tool_arguments is None:
        return None
    if context.pending_tool_name in WRITE_TOOL_NAMES:
        try:
            result = propose_agent_write(
                services,
                context,
                context.pending_tool_call_id,
                context.pending_tool_name,
                context.pending_tool_arguments,
            )
        except ValidationError:
            error = "malformed arguments for UpdateApplicationStatus"
            persist_tool_error(services, context.pending_tool_call_id, error)
            return context, error
        if result.confirmation_prompt is not None:
            _deliver_confirmation_prompt(services, context, result.confirmation_prompt)
        return context, None
    error, result = execute_tool(
        services,
        context,
        context.pending_tool_call_id,
        context.pending_tool_name,
        context.pending_tool_arguments,
    )
    if result is None:
        return context, error
    tool_results.append(
        PromptToolResult(
            tool_call_id=context.pending_tool_call_id,
            data=result.data,
            context_refs=result.context_refs,
        )
    )
    return _with_results(services.database, context, tool_results), None


def _resume_before_turns(
    services: RuntimeServices,
    context: RunContext,
    tool_results: list[PromptToolResult],
) -> tuple[RunContext, str | None, bool]:
    pending_write = context.pending_tool_name in WRITE_TOOL_NAMES
    resumed = _resume_pending_tool(services, context, tool_results)
    if resumed is None:
        return context, None, False
    resumed_context, error = resumed
    return resumed_context, error, pending_write and error is None


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


def _execute_requested_tool(
    services: RuntimeServices,
    context: RunContext,
    call_id: str,
    name: str,
    arguments: ToolArguments,
) -> tuple[ToolExecution | None, str, bool]:
    if name in WRITE_TOOL_NAMES:
        try:
            result = propose_agent_write(services, context, call_id, name, arguments)
        except ValidationError:
            error = "malformed arguments for UpdateApplicationStatus"
            persist_tool_error(services, call_id, error)
            return None, error, False
        return result, "", True
    error, result = execute_tool(services, context, call_id, name, arguments)
    return result, error, False


def run_turns(
    services: RuntimeServices,
    context: RunContext,
) -> tuple[str | None, str | None, bool]:
    """Run a bounded sequence of LLM responses and persisted tool calls."""
    started = time.monotonic()
    context = update_active_context(services.database, context)
    tool_results = list(context.tool_results)
    context, tool_error, suspended = _resume_before_turns(services, context, tool_results)
    if tool_error is not None:
        return None, tool_error, False
    if suspended:
        return None, None, False
    answer: str | None = None
    error: str | None = None
    retryable = False
    for sequence in range(context.next_sequence, services.max_tool_calls + 1):
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
        if response.tool_call is None:
            error = "conversation response contained neither answer nor tool call"
            break
        call_id = persist_tool_call(
            services,
            context,
            sequence,
            response.tool_call.name,
            response.tool_call.arguments,
        )
        result, tool_error, suspended = _execute_requested_tool(
            services,
            context,
            call_id,
            response.tool_call.name,
            response.tool_call.arguments,
        )
        if suspended:
            if result is not None and result.confirmation_prompt is not None:
                _deliver_confirmation_prompt(services, context, result.confirmation_prompt)
            return None, None, False
        if result is None:
            error = tool_error
            break
        tool_results.append(
            PromptToolResult(
                tool_call_id=call_id,
                data=result.data,
                context_refs=result.context_refs,
            )
        )
        context = _with_results(services.database, context, tool_results)
    return (
        answer,
        error or (None if answer is not None else "agent run exceeded tool-call limit"),
        retryable if error is not None else False,
    )
