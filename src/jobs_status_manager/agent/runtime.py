"""Durable sequential conversation agent runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from jobs_status_manager.agent.contracts import (
    MAX_RUN_SECONDS,
    MAX_TOOL_CALLS,
    MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_SECONDS,
    AgentRunState,
)
from jobs_status_manager.agent.delivery import (
    DeliveryOutcome,
    complete_run,
    persist_final_message,
    record_command_delivery,
    record_delivery,
)
from jobs_status_manager.agent.models import AgentRun
from jobs_status_manager.agent.runtime_support import load_context
from jobs_status_manager.agent.tools import execute
from jobs_status_manager.agent.turn_runtime import run_turns
from jobs_status_manager.agent.write_contracts import ToolExecution
from jobs_status_manager.agent.write_runtime import (
    finalize_rejected_action,
    resume_confirmed_action,
)
from jobs_status_manager.application_core.domain import ActionResolution, UserId
from jobs_status_manager.application_core.errors import (
    AmbiguousPendingActionError,
    PendingActionNotFoundError,
)
from jobs_status_manager.application_core.router import route_command
from jobs_status_manager.application_core.service import resolve_confirmation
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error

if TYPE_CHECKING:
    from jobs_status_manager.agent.runtime_support import RunContext
    from jobs_status_manager.infrastructure.adapters.protocols import (
        ChromaAdapter,
        EmbeddingAdapter,
        LLMAdapter,
        QQGateway,
    )
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator


type ToolExecutor = Callable[..., ToolExecution]


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    """Dependencies and limits for one runtime cycle."""

    database: Database
    llm: LLMAdapter
    qq: QQGateway
    clock: Clock
    ids: IdGenerator
    embedding: EmbeddingAdapter | None = None
    chroma: ChromaAdapter | None = None
    max_tool_calls: int = MAX_TOOL_CALLS
    max_run_seconds: int = MAX_RUN_SECONDS
    tool_timeout_seconds: float = MAX_TOOL_SECONDS
    max_result_chars: int = MAX_TOOL_RESULT_CHARS
    tool_executor: ToolExecutor = execute


def process_run(services: RuntimeServices, run_id: str) -> None:
    """Process one persisted run with sequential bounded tool calls."""
    with transaction(services.database) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            return
        if run.state == AgentRunState.RUNNING.value and run.started_at is None:
            run.started_at = services.clock.now()
            run.attempt_count += 1
            run.next_retry_at = None
    context = load_context(services.database, run_id)
    if context is None:
        return
    if context.state == AgentRunState.WAITING_USER_CONFIRMATION.value:
        return
    answer = (
        context.final_message_content
        if context.state == AgentRunState.DELIVERY_PENDING.value
        else None
    )
    error: str | None = None
    if answer is None:
        command = route_command(context.user_message)
        if command.resolution is not None:
            _process_confirmation(services, context, command.resolution)
            return
        answer, error = run_turns(services, context)
        with transaction(services.database) as session:
            run = session.get(AgentRun, context.run_id)
            if run is not None and run.state == AgentRunState.WAITING_USER_CONFIRMATION.value:
                return
    if answer is not None:
        _deliver_answer(services, context, answer)
    else:
        complete_run(services, context, answer, error, DeliveryOutcome())


def _deliver_answer(services: RuntimeServices, context: RunContext, answer: str) -> None:
    persist_final_message(services, context, answer)
    try:
        delivery = services.qq.push(context.user_id, answer)
    except (RuntimeError, ValueError, TimeoutError) as exception:
        outcome = DeliveryOutcome("FAILED", safe_external_error(exception))
    else:
        outcome = DeliveryOutcome(
            "SENT" if delivery.success else "FAILED",
            None if delivery.success else "QQ provider rejected delivery",
            delivery.provider_message_id,
        )
    record_delivery(services, context, outcome)


def _deliver_command_answer(
    services: RuntimeServices,
    context: RunContext,
    answer: str,
) -> None:
    try:
        delivery = services.qq.push(context.user_id, answer)
    except (RuntimeError, ValueError, TimeoutError) as exception:
        outcome = DeliveryOutcome("FAILED", safe_external_error(exception))
    else:
        outcome = DeliveryOutcome(
            "SENT" if delivery.success else "FAILED",
            None if delivery.success else "QQ provider rejected delivery",
            delivery.provider_message_id,
        )
    record_command_delivery(services, context, answer, outcome)


def _process_confirmation(
    services: RuntimeServices,
    context: RunContext,
    resolution: ActionResolution,
) -> None:
    try:
        result = resolve_confirmation(
            services.database,
            user_id=UserId(context.user_id),
            command_text=context.user_message,
            clock=services.clock,
            ids=services.ids,
        )
    except (AmbiguousPendingActionError, PendingActionNotFoundError) as exception:
        _deliver_command_answer(services, context, str(exception))
        return
    if result is None:
        _deliver_command_answer(services, context, "无法识别确认命令。")
        return
    if resolution is ActionResolution.CONFIRM and result.state.value == "CONFIRMED":
        original_run_id = resume_confirmed_action(services, result.action_id)
        if original_run_id is not None:
            process_run(services, original_run_id)
        _deliver_command_answer(services, context, "已确认; 正在处理.")
        return
    if resolution is ActionResolution.REJECT and result.state.value == "REJECTED":
        original_run_id = finalize_rejected_action(
            services.database, result.action_id, services.clock, services.ids
        )
        if original_run_id is not None:
            process_run(services, original_run_id)
        _deliver_command_answer(services, context, "已拒绝; 未修改求职状态.")
        return
    _deliver_command_answer(services, context, result.message)


def recover_runs(database: Database, clock: Clock, max_run_seconds: int = MAX_RUN_SECONDS) -> int:
    """Fail stale RUNNING rows so a restart cannot strand a session."""
    with transaction(database) as session:
        now = clock.now()
        rows = list(
            session.scalars(select(AgentRun).where(AgentRun.state == AgentRunState.RUNNING.value))
        )
        changed = 0
        for run in rows:
            started = run.started_at or run.created_at
            started = started.replace(tzinfo=now.tzinfo) if started.tzinfo is None else started
            if (now - started).total_seconds() > max_run_seconds:
                run.state = AgentRunState.FAILED.value
                run.error = "agent run recovered after timeout"
                run.completed_at = now
                run.next_retry_at = None
                changed += 1
        return changed


def resume_confirmed_run(
    services: RuntimeServices,
    action_id: str,
) -> None:
    """Execute a confirmed action and continue its original AgentRun."""
    run_id = resume_confirmed_action(services, action_id)
    if run_id is not None:
        process_run(services, run_id)
