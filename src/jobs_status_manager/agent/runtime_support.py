"""Persistence boundaries for the conversation runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from jobs_status_manager.agent.contracts import (
    MAX_RECENT_MESSAGES,
    SYSTEM_PROMPT,
    AgentRunState,
    PromptMessage,
    PromptToolResult,
)
from jobs_status_manager.agent.models import AgentRun, ConversationMessage, ToolCall, ToolResult
from jobs_status_manager.agent.models import Session as AgentSession
from jobs_status_manager.agent.tool_runtime import (
    execute_tool,
    persist_tool_call,
    persist_tool_error,
)
from jobs_status_manager.infrastructure.database.transactions import transaction

if TYPE_CHECKING:
    from jobs_status_manager.agent.tools import ToolArguments
    from jobs_status_manager.infrastructure.database.connection import Database

__all__ = [
    "RunContext",
    "execute_tool",
    "load_context",
    "persist_tool_call",
    "persist_tool_error",
    "update_active_context",
]


@dataclass(frozen=True, slots=True)
class RunContext:
    """Immutable input snapshot for one external LLM call sequence."""

    run_id: str
    session_id: str
    user_id: str
    system_prompt: str
    session_summary: str
    active_application_id: str | None
    active_mail_id: str | None
    active_knowledge_document_id: str | None
    recent_messages: tuple[PromptMessage, ...]
    user_message: str
    tool_results: tuple[PromptToolResult, ...]
    next_sequence: int
    pending_tool_call_id: str | None
    pending_tool_name: str | None
    pending_tool_arguments: ToolArguments | None
    final_message_id: str | None
    final_message_content: str | None
    state: str


def load_context(database: Database, run_id: str) -> RunContext | None:
    """Load and validate the durable input snapshot for a running agent."""
    with transaction(database) as session:
        run = session.get(AgentRun, run_id)
        if run is None or run.state not in (
            AgentRunState.RUNNING.value,
            AgentRunState.DELIVERY_PENDING.value,
        ):
            return None
        message = session.get(ConversationMessage, run.user_message_id)
        if message is None:
            run.state = AgentRunState.FAILED.value
            run.error = "missing user message"
            return None
        user_id = session.scalar(
            select(AgentSession.user_id).where(AgentSession.id == run.session_id)
        )
        if user_id is None:
            run.state = AgentRunState.FAILED.value
            run.error = "missing session user"
            return None
        session_row = session.get(AgentSession, run.session_id)
        if session_row is None:
            run.state = AgentRunState.FAILED.value
            run.error = "missing session"
            return None
        recent_messages = tuple(
            PromptMessage(role=row.role, content=row.content)
            for row in session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.session_id == run.session_id)
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
            ).all()[-MAX_RECENT_MESSAGES:]
        )
        tool_results = tuple(
            PromptToolResult(data=result.data, context_refs=result.context_refs)
            for result in session.scalars(
                select(ToolResult)
                .join(ToolCall, ToolResult.tool_call_id == ToolCall.id)
                .where(ToolCall.agent_run_id == run.id, ToolResult.error.is_(None))
                .order_by(ToolCall.sequence)
            )
        )
        calls = list(
            session.scalars(
                select(ToolCall).where(ToolCall.agent_run_id == run.id).order_by(ToolCall.sequence)
            )
        )
        pending_call = next(
            (
                call
                for call in calls
                if session.scalar(select(ToolResult.id).where(ToolResult.tool_call_id == call.id))
                is None
            ),
            None,
        )
        final_message = (
            session.get(ConversationMessage, run.final_message_id)
            if run.final_message_id is not None
            else None
        )
        return RunContext(
            run_id=run_id,
            session_id=run.session_id,
            user_id=user_id,
            system_prompt=SYSTEM_PROMPT,
            session_summary=session_row.summary,
            active_application_id=session_row.active_application_id,
            active_mail_id=session_row.active_mail_id,
            active_knowledge_document_id=session_row.active_knowledge_document_id,
            recent_messages=recent_messages,
            user_message=message.content,
            tool_results=tool_results,
            next_sequence=len(calls) + 1,
            pending_tool_call_id=None if pending_call is None else pending_call.id,
            pending_tool_name=None if pending_call is None else pending_call.tool_name,
            pending_tool_arguments=None if pending_call is None else pending_call.arguments,
            final_message_id=run.final_message_id,
            final_message_content=None if final_message is None else final_message.content,
            state=run.state,
        )


def update_active_context(database: Database, context: RunContext) -> RunContext:
    """Apply persisted tool references to the session and return a fresh context."""
    with transaction(database) as session:
        session_row = session.get(AgentSession, context.session_id)
        if session_row is None:
            return context
        refs = context.tool_results[-1].context_refs if context.tool_results else {}
        if "application_id" in refs:
            session_row.active_application_id = refs["application_id"]
        if "mail_id" in refs:
            session_row.active_mail_id = refs["mail_id"]
        if "knowledge_document_id" in refs:
            session_row.active_knowledge_document_id = refs["knowledge_document_id"]
        return RunContext(
            run_id=context.run_id,
            session_id=context.session_id,
            user_id=context.user_id,
            system_prompt=context.system_prompt,
            session_summary=session_row.summary,
            active_application_id=session_row.active_application_id,
            active_mail_id=session_row.active_mail_id,
            active_knowledge_document_id=session_row.active_knowledge_document_id,
            recent_messages=context.recent_messages,
            user_message=context.user_message,
            tool_results=context.tool_results,
            next_sequence=context.next_sequence,
            pending_tool_call_id=context.pending_tool_call_id,
            pending_tool_name=context.pending_tool_name,
            pending_tool_arguments=context.pending_tool_arguments,
            final_message_id=context.final_message_id,
            final_message_content=context.final_message_content,
            state=context.state,
        )
