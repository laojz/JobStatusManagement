"""Persistence bridge between Agent write proposals and Application Core."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, assert_never

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from jobs_status_manager.agent.contracts import AgentRunState
from jobs_status_manager.agent.models import AgentRun, ToolResult
from jobs_status_manager.agent.tools import WRITE_TOOL_NAME, WriteToolName
from jobs_status_manager.agent.write_contracts import (
    UpdateApplicationStatusArguments,
)
from jobs_status_manager.agent.write_proposals import propose_knowledge_write, propose_status_write
from jobs_status_manager.application_core.models import PendingAction
from jobs_status_manager.application_core.repositories import find_application
from jobs_status_manager.application_core.service import execute_status_update
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.knowledge.service import (
    KnowledgeServices,
    execute_add,
    execute_remove,
)

if TYPE_CHECKING:
    from jobs_status_manager.agent.runtime import RuntimeServices
    from jobs_status_manager.agent.runtime_support import RunContext
    from jobs_status_manager.agent.tools import ToolArguments
    from jobs_status_manager.agent.write_contracts import ToolExecution
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator


def propose_agent_write(
    services: RuntimeServices,
    context: RunContext,
    call_id: str,
    name: str,
    arguments: ToolArguments,
) -> ToolExecution:
    """Dispatch one registered confirmation-gated proposal."""
    try:
        parsed_name = TypeAdapter(WriteToolName).validate_python(name)
    except ValidationError as error:
        message = f"write tool is not registered: {name}"
        raise PermissionError(message) from error
    match parsed_name:
        case "UpdateApplicationStatus":
            return propose_status_write(services, context, call_id, arguments)
        case "AddKnowledge" | "RemoveKnowledge":
            return propose_knowledge_write(services, context, call_id, parsed_name, arguments)
        case unreachable:
            assert_never(unreachable)


def _execute_confirmed_action(
    services: RuntimeServices,
    action_id: str,
    action_type: WriteToolName,
) -> tuple[str | None, bool, str]:
    match action_type:
        case "UpdateApplicationStatus":
            result = execute_status_update(
                services.database,
                action_id=action_id,
                clock=services.clock,
                ids=services.ids,
            )
            return result.application_id, result.changed, result.message
        case "AddKnowledge":
            result = execute_add(
                KnowledgeServices(services.database, services.clock, services.ids), action_id
            )
            return result.document_id, True, result.message
        case "RemoveKnowledge":
            result = execute_remove(
                KnowledgeServices(services.database, services.clock, services.ids), action_id
            )
            return result.document_id, True, result.message
        case unreachable:
            assert_never(unreachable)


def resume_confirmed_action(services: RuntimeServices, action_id: str) -> str | None:
    """Execute frozen arguments and make the owning run resumable."""
    with transaction(services.database) as session:
        selected = session.get(PendingAction, action_id)
        if selected is None:
            return None
        action_type = selected.action_type
    try:
        parsed_action_type = TypeAdapter(WriteToolName).validate_python(action_type)
    except ValidationError as error:
        message = f"unsupported pending action: {action_type}"
        raise PermissionError(message) from error
    entity_id, changed, result_message = _execute_confirmed_action(
        services,
        action_id,
        parsed_action_type,
    )
    with transaction(services.database) as session:
        action = session.get(PendingAction, action_id)
        if action is None or action.agent_run_id is None or action.tool_call_id is None:
            return None
        tool_result = session.scalar(
            select(ToolResult).where(ToolResult.tool_call_id == action.tool_call_id)
        )
        application_id = entity_id if action_type == WRITE_TOOL_NAME else None
        knowledge_document_id = entity_id if action_type != WRITE_TOOL_NAME else None
        if application_id is None and action_type == WRITE_TOOL_NAME:
            arguments = UpdateApplicationStatusArguments.model_validate(action.resolved_arguments)
            application = find_application(session, action.user_id, arguments.reference())
            application_id = None if application is None else application.id
        data = json.dumps(
            {
                "action_id": action.id,
                "state": action.state,
                "message": result_message,
                "application_id": application_id,
                "knowledge_document_id": knowledge_document_id,
                "changed": changed,
            },
            ensure_ascii=False,
        )
        context_refs: dict[str, str | None] = {
            "pending_action_id": action.id,
            "application_id": application_id,
            "resolution_state": "COMPLETED",
            "knowledge_document_id": knowledge_document_id,
        }
        if tool_result is None:
            tool_result = ToolResult(
                id=str(services.ids.new_id()),
                tool_call_id=action.tool_call_id,
                data=data,
                context_refs=context_refs,
                error=None,
                started_at=services.clock.now(),
                completed_at=services.clock.now(),
                created_at=services.clock.now(),
            )
            session.add(tool_result)
        else:
            tool_result.data = data
            tool_result.context_refs = context_refs
            tool_result.error = None
            tool_result.completed_at = services.clock.now()
        run = session.get(AgentRun, action.agent_run_id)
        if run is not None and run.state == AgentRunState.WAITING_USER_CONFIRMATION.value:
            run.final_message_id = None
            run.state = AgentRunState.RUNNING.value
        return action.agent_run_id


def record_confirmation_delivery(
    services: RuntimeServices,
    run_id: str,
    *,
    success: bool,
    error: str | None = None,
) -> None:
    """Persist whether the user received the confirmation prompt."""
    with transaction(services.database) as session:
        run = session.get(AgentRun, run_id)
        if run is not None:
            run.delivery_state = "SENT" if success else "FAILED"
            run.delivery_error = error


def finalize_rejected_action(
    database: Database,
    action_id: str,
    clock: Clock,
    ids: IdGenerator,
) -> str | None:
    """Persist rejection and make the owning run resumable."""
    with transaction(database) as session:
        action = session.get(PendingAction, action_id)
        if action is None or action.agent_run_id is None or action.tool_call_id is None:
            return None
        tool_result = session.scalar(
            select(ToolResult).where(ToolResult.tool_call_id == action.tool_call_id)
        )
        data = json.dumps(
            {"action_id": action.id, "state": action.state, "message": "rejected"},
            ensure_ascii=False,
        )
        context_refs: dict[str, str | None] = {
            "pending_action_id": action.id,
            "resolution_state": "REJECTED",
        }
        if tool_result is None:
            tool_result = ToolResult(
                id=str(ids.new_id()),
                tool_call_id=action.tool_call_id,
                data=data,
                context_refs=context_refs,
                error=None,
                started_at=clock.now(),
                completed_at=clock.now(),
                created_at=clock.now(),
            )
            session.add(tool_result)
        else:
            tool_result.data = data
            tool_result.context_refs = context_refs
            tool_result.error = None
            tool_result.completed_at = clock.now()
        run = session.get(AgentRun, action.agent_run_id)
        if run is not None:
            run.final_message_id = None
            run.state = AgentRunState.RUNNING.value
        return action.agent_run_id
