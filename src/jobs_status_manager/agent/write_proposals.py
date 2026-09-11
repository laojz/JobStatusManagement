"""Confirmation-gated Agent write proposal persistence."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, assert_never

from sqlalchemy import select

from jobs_status_manager.agent.contracts import AgentRunState
from jobs_status_manager.agent.models import AgentRun, ConversationMessage, ToolResult
from jobs_status_manager.agent.tools import AddKnowledgeToolArguments
from jobs_status_manager.agent.write_contracts import (
    ToolExecution,
    UpdateApplicationStatusArguments,
)
from jobs_status_manager.application_core.domain import UserId
from jobs_status_manager.application_core.models import PendingAction
from jobs_status_manager.application_core.proposals import create_status_proposal
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.knowledge.contracts import RemoveKnowledgeArguments
from jobs_status_manager.knowledge.service import KnowledgeServices, propose_add, propose_remove

if TYPE_CHECKING:
    from jobs_status_manager.agent.runtime import RuntimeServices
    from jobs_status_manager.agent.runtime_support import RunContext
    from jobs_status_manager.agent.tools import ToolArguments

type KnowledgeWriteToolName = Literal["AddKnowledge", "RemoveKnowledge"]


def propose_status_write(
    services: RuntimeServices,
    context: RunContext,
    call_id: str,
    arguments: ToolArguments,
) -> ToolExecution:
    """Freeze a status proposal and suspend its owning run."""
    parsed = UpdateApplicationStatusArguments.model_validate(arguments)
    with transaction(services.database) as session:
        action = create_status_proposal(
            session,
            user_id=UserId(context.user_id),
            source_id=call_id,
            arguments=parsed,
            clock=services.clock,
            ids=services.ids,
            source_type="agent_tool",
        )
        action.session_id = context.session_id
        action.agent_run_id = context.run_id
        action.tool_call_id = call_id
        run = session.get(AgentRun, context.run_id)
        prompt = (
            f"{action.display_summary}\n确认码: {action.confirmation_code}\n"
            f"发送“确认 {action.confirmation_code}”或“拒绝 {action.confirmation_code}”。"
            "未确认前不会修改求职状态。"
        )
        if run is not None:
            run.state = AgentRunState.WAITING_USER_CONFIRMATION.value
            run.delivery_state = "PENDING"
            if run.final_message_id is None:
                message_id = str(services.ids.new_id())
                session.add(
                    ConversationMessage(
                        id=message_id,
                        session_id=context.session_id,
                        role="assistant",
                        content=prompt,
                        provider_event_id=None,
                        created_at=services.clock.now(),
                    )
                )
                session.flush()
                run.final_message_id = message_id
        data = json.dumps(
            {
                "pending_action_id": action.id,
                "confirmation_code": action.confirmation_code,
                "display_summary": action.display_summary,
                "message": "未确认前不会修改求职状态",
            },
            ensure_ascii=False,
        )
        context_refs: dict[str, str | None] = {
            "pending_action_id": action.id,
            "confirmation_code": action.confirmation_code,
            "resolution_state": "PENDING",
        }
        tool_result = session.scalar(select(ToolResult).where(ToolResult.tool_call_id == call_id))
        if tool_result is None:
            session.add(
                ToolResult(
                    id=str(services.ids.new_id()),
                    tool_call_id=call_id,
                    data=data,
                    context_refs=context_refs,
                    error=None,
                    started_at=services.clock.now(),
                    completed_at=services.clock.now(),
                    created_at=services.clock.now(),
                )
            )
    return ToolExecution(data, context_refs, action.id, prompt)


def propose_knowledge_write(
    services: RuntimeServices,
    context: RunContext,
    call_id: str,
    name: KnowledgeWriteToolName,
    arguments: ToolArguments,
) -> ToolExecution:
    """Freeze a knowledge proposal and suspend its owning run."""
    knowledge = KnowledgeServices(services.database, services.clock, services.ids)
    match name:
        case "AddKnowledge":
            parsed = AddKnowledgeToolArguments.model_validate(arguments).domain_arguments()
            action = propose_add(knowledge, context.user_id, call_id, parsed)
        case "RemoveKnowledge":
            parsed_remove = RemoveKnowledgeArguments.model_validate(arguments)
            action = propose_remove(knowledge, context.user_id, call_id, parsed_remove)
        case unreachable:
            assert_never(unreachable)
    prompt = (
        f"{action.display_summary}\n确认码: {action.confirmation_code}\n"
        f"发送“确认 {action.confirmation_code}”或“拒绝 {action.confirmation_code}”。"
        "未确认前不会修改知识库。"
    )
    data = json.dumps(
        {
            "pending_action_id": action.id,
            "confirmation_code": action.confirmation_code,
            "display_summary": action.display_summary,
        },
        ensure_ascii=False,
    )
    refs: dict[str, str | None] = {
        "pending_action_id": action.id,
        "confirmation_code": action.confirmation_code,
        "resolution_state": "PENDING",
    }
    with transaction(services.database) as session:
        persisted = session.get(PendingAction, action.id)
        run = session.get(AgentRun, context.run_id)
        if persisted is not None:
            persisted.session_id = context.session_id
            persisted.agent_run_id = context.run_id
            persisted.tool_call_id = call_id
        if run is not None:
            run.state = AgentRunState.WAITING_USER_CONFIRMATION.value
            run.delivery_state = "PENDING"
        if session.scalar(select(ToolResult).where(ToolResult.tool_call_id == call_id)) is None:
            session.add(
                ToolResult(
                    id=str(services.ids.new_id()),
                    tool_call_id=call_id,
                    data=data,
                    context_refs=refs,
                    error=None,
                    started_at=services.clock.now(),
                    completed_at=services.clock.now(),
                    created_at=services.clock.now(),
                )
            )
    return ToolExecution(data, refs, action.id, prompt)
