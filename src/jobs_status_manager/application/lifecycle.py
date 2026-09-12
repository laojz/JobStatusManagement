"""Starlette application lifecycle."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Protocol, TypedDict

import anyio
import structlog
from sqlalchemy import exists, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from jobs_status_manager.agent.botpy_ingestion import create_botpy_event_handler
from jobs_status_manager.agent.models import AgentRun
from jobs_status_manager.agent.runtime import (
    RuntimeServices,
    process_run,
    recover_runs,
    resume_confirmed_run,
)
from jobs_status_manager.agent.webhook import WebhookContext, receive_webhook
from jobs_status_manager.agent.write_recovery import (
    reconcile_terminal_actions,
    retry_confirmation_prompts,
)
from jobs_status_manager.application.health import health
from jobs_status_manager.application_core.service import recover_pending_actions
from jobs_status_manager.event_pipeline import EventServices, publish_once
from jobs_status_manager.identity.models import MailAccount
from jobs_status_manager.infrastructure.adapters.factory import create_owned_adapters
from jobs_status_manager.infrastructure.clock import SystemClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.ids import UUIDGenerator
from jobs_status_manager.infrastructure.logging import configure_logging
from jobs_status_manager.infrastructure.qq_botpy import create_botpy_runtime
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.knowledge.index import (
    IndexRetryServices,
    IndexServices,
    cleanup_removed_once,
    index_once,
    recover_stale_indexing,
)
from jobs_status_manager.mail_poller import poll_once
from jobs_status_manager.mail_service import MailServices
from jobs_status_manager.notifications import NotificationServices, dispatch_once, recover_stale

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from starlette.requests import Request

    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.infrastructure.adapters.protocols import (
        ChromaAdapter,
        EmbeddingAdapter,
        IMAPGateway,
        LLMAdapter,
        QQGateway,
    )
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.ids import IdGenerator
    from jobs_status_manager.infrastructure.qq_botpy import LifespanBotpyRuntime


logger = structlog.get_logger(__name__)
WORKER_INTERVAL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class LifecycleAdapters:
    """Explicit external capabilities supplied to application workers."""

    imap: IMAPGateway | None = None
    llm: LLMAdapter | None = None
    qq: QQGateway | None = None
    clock: Clock | None = None
    ids: IdGenerator | None = None
    embedding: EmbeddingAdapter | None = None
    chroma: ChromaAdapter | None = None
    qq_botpy_factory: Callable[[AppSettings], LifespanBotpyRuntime] | None = None


class ClosableResource(Protocol):
    """Synchronous resource that can be closed during lifespan teardown."""

    def close(self) -> None:
        """Release owned resource state."""


class DisposableResource(Protocol):
    """Synchronous resource that can dispose its pooled state."""

    def dispose(self) -> None:
        """Release pooled resource state."""


async def _cleanup_lifecycle_resources(
    qq_runtime: LifespanBotpyRuntime | None,
    owned: ClosableResource | None,
    database: DisposableResource,
) -> None:
    """Attempt every shutdown action even when cancellation or close errors occur."""
    cleanup_error: BaseException | None = None
    with anyio.CancelScope(shield=True):
        if qq_runtime is not None:
            try:
                await qq_runtime.close()
            except BaseException as error:
                cleanup_error = error
                logger.exception("qq_runtime_close_failed")
        if owned is not None:
            try:
                owned.close()
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
                logger.exception("owned_adapter_close_failed")
        try:
            database.dispose()
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error
            logger.exception("database_dispose_failed")
    if cleanup_error is not None:
        raise cleanup_error


def _recover_notifications(database: Database, adapters: LifecycleAdapters) -> None:
    """Run the persisted stale-notification recovery scan."""
    clock = adapters.clock if adapters.clock is not None else SystemClock()
    ids = adapters.ids if adapters.ids is not None else UUIDGenerator()
    recover_stale(NotificationServices(database, clock, ids))


def _active_account_id(database: Database) -> str | None:
    with database.engine.connect() as connection:
        return connection.execute(
            select(MailAccount.id).where(MailAccount.is_active).order_by(MailAccount.id)
        ).scalar_one_or_none()


def _poll_cycle(database: Database, adapters: LifecycleAdapters) -> None:
    """Run one injected IMAP cycle when an active account exists."""
    if adapters.imap is None:
        return
    account_id = _active_account_id(database)
    if account_id is None:
        return
    poll_once(MailServices(database, SystemClock(), UUIDGenerator()), account_id, adapters.imap)


def _publish_cycle(database: Database, adapters: LifecycleAdapters) -> None:
    """Run one injected LLM-backed outbox cycle."""
    if adapters.llm is None:
        return
    services = MailServices(database, SystemClock(), UUIDGenerator())
    publish_once(EventServices(services, adapters.llm))


def _dispatch_cycle(database: Database, adapters: LifecycleAdapters) -> None:
    """Run one injected QQ notification cycle."""
    if adapters.qq is None:
        return
    dispatch_once(NotificationServices(database, SystemClock(), UUIDGenerator()), adapters.qq)


def _agent_cycle(database: Database, adapters: LifecycleAdapters, settings: AppSettings) -> None:
    """Process one durable agent run and recover stale work."""
    clock = adapters.clock if adapters.clock is not None else SystemClock()
    ids = adapters.ids if adapters.ids is not None else UUIDGenerator()
    recover_runs(database, clock, settings.agent_max_run_seconds)
    recovered_actions = recover_pending_actions(database, clock=clock, ids=ids)
    if adapters.llm is None or adapters.qq is None:
        return
    services = RuntimeServices(
        database=database,
        llm=adapters.llm,
        qq=adapters.qq,
        clock=clock,
        ids=ids,
        embedding=adapters.embedding,
        chroma=adapters.chroma,
        max_tool_calls=settings.agent_max_tool_calls,
        max_run_seconds=settings.agent_max_run_seconds,
        tool_timeout_seconds=settings.agent_tool_timeout_seconds,
        max_result_chars=settings.agent_max_result_chars,
        max_delivery_attempts=settings.agent_max_delivery_attempts,
    )
    for result in recovered_actions:
        resume_confirmed_run(services, result.action_id)
    for run_id in reconcile_terminal_actions(services):
        process_run(services, run_id)
    retry_confirmation_prompts(services)
    with database.engine.begin() as connection:
        run_id = connection.execute(
            select(AgentRun.id)
            .where(
                (AgentRun.state == "RUNNING")
                | (
                    (AgentRun.state == "DELIVERY_PENDING")
                    & (AgentRun.next_retry_at.is_(None) | (AgentRun.next_retry_at <= clock.now()))
                )
            )
            .order_by(AgentRun.created_at)
            .limit(1)
        ).scalar_one_or_none()
        if run_id is None:
            active_run = aliased(AgentRun)
            queued_id = connection.execute(
                select(AgentRun.id)
                .where(
                    AgentRun.state == "QUEUED",
                    ~exists(
                        select(active_run.id).where(
                            active_run.session_id == AgentRun.session_id,
                            active_run.state.in_(
                                (
                                    "RUNNING",
                                    "QUEUED",
                                    "DELIVERY_PENDING",
                                    "WAITING_USER_CONFIRMATION",
                                )
                            ),
                        )
                    ),
                )
                .order_by(AgentRun.created_at)
                .limit(1)
            ).scalar_one_or_none()
            if queued_id is not None:
                changed = connection.execute(
                    update(AgentRun)
                    .where(AgentRun.id == queued_id, AgentRun.state == "QUEUED")
                    .values(
                        state="RUNNING",
                        started_at=clock.now(),
                        attempt_count=AgentRun.attempt_count + 1,
                    )
                ).rowcount
                run_id = queued_id if changed == 1 else None
    if run_id is not None:
        process_run(services, run_id)


def _startup_recovery(
    database: Database, adapters: LifecycleAdapters, settings: AppSettings
) -> None:
    """Reconcile durable agent state before normal worker loops begin."""
    clock = adapters.clock if adapters.clock is not None else SystemClock()
    ids = adapters.ids if adapters.ids is not None else UUIDGenerator()
    _recover_notifications(database, adapters)
    recover_stale_indexing(IndexRetryServices(database, clock))
    recover_runs(database, clock, settings.agent_max_run_seconds)
    recover_pending_actions(database, clock=clock, ids=ids)


def _knowledge_cycle(database: Database, adapters: LifecycleAdapters) -> None:
    if adapters.embedding is None or adapters.chroma is None:
        return
    clock = adapters.clock if adapters.clock is not None else SystemClock()
    services = IndexServices(database, clock, adapters.embedding, adapters.chroma)
    cleanup_removed_once(services)
    index_once(services)


async def _worker_loop(name: str, cycle: Callable[[], None]) -> None:
    """Repeat one synchronous durable cycle without blocking the event loop."""
    while True:
        try:
            await anyio.to_thread.run_sync(cycle)
        except (RuntimeError, ValueError, SQLAlchemyError) as error:
            logger.warning("phase2_worker_failed", worker=name, error=safe_external_error(error))
        await anyio.sleep(WORKER_INTERVAL_SECONDS)


class ApplicationState(TypedDict):
    """Shared read-only lifecycle state."""

    database: Database
    project_root: Path


def create_app(
    settings: AppSettings,
    project_root: Path,
    adapters: LifecycleAdapters | None = None,
) -> Starlette:
    """Create the Starlette application with explicit Phase 2 worker wiring."""
    supplied_adapters = adapters if adapters is not None else LifecycleAdapters()
    effective_adapters = supplied_adapters
    qq_runtime: LifespanBotpyRuntime | None = None

    async def qq_webhook(request: Request) -> Response:
        if qq_runtime is not None:
            transport_response = await qq_runtime.handle_http_request(
                await request.body(),
                dict(request.headers),
            )
            return Response(
                transport_response.body,
                status_code=transport_response.status,
                headers=dict(transport_response.headers),
                media_type="application/json",
            )
        return await receive_webhook(
            request,
            WebhookContext(
                request.state.database,
                settings,
                effective_adapters.clock if effective_adapters.clock is not None else SystemClock(),
                effective_adapters.ids if effective_adapters.ids is not None else UUIDGenerator(),
            ),
            effective_adapters,
        )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[ApplicationState]:
        nonlocal effective_adapters, qq_runtime
        configure_logging(settings.log_level)
        database = Database(settings.database_path)
        owned = None
        try:
            if supplied_adapters.qq is None and supplied_adapters.qq_botpy_factory is not None:
                qq_runtime = supplied_adapters.qq_botpy_factory(settings)
            elif (
                supplied_adapters.qq is None
                and settings.qq_enabled
                and settings.qq_app_id is not None
                and settings.qq_app_secret is not None
                and settings.qq_token_base_url is not None
            ):
                qq_context = WebhookContext(
                    database,
                    settings,
                    supplied_adapters.clock
                    if supplied_adapters.clock is not None
                    else SystemClock(),
                    supplied_adapters.ids if supplied_adapters.ids is not None else UUIDGenerator(),
                )
                qq_runtime = create_botpy_runtime(
                    settings,
                    create_botpy_event_handler(qq_context),
                )
            owned = (
                None
                if supplied_adapters.embedding is not None and supplied_adapters.chroma is not None
                else create_owned_adapters(settings)
            )
            effective_adapters = LifecycleAdapters(
                imap=supplied_adapters.imap,
                llm=supplied_adapters.llm,
                qq=(
                    supplied_adapters.qq
                    if supplied_adapters.qq is not None
                    else (qq_runtime.gateway if qq_runtime is not None else None)
                ),
                clock=supplied_adapters.clock,
                ids=supplied_adapters.ids,
                embedding=(
                    supplied_adapters.embedding
                    if supplied_adapters.embedding is not None
                    else (owned.embedding if owned is not None else None)
                ),
                chroma=(
                    supplied_adapters.chroma
                    if supplied_adapters.chroma is not None
                    else (owned.chroma if owned is not None else None)
                ),
            )
            _startup_recovery(database, effective_adapters, settings)
            async with anyio.create_task_group() as task_group:
                if qq_runtime is not None:
                    await qq_runtime.start(task_group)
                task_group.start_soon(
                    _worker_loop,
                    "imap-poller",
                    partial(_poll_cycle, database, effective_adapters),
                )
                task_group.start_soon(
                    _worker_loop,
                    "outbox-publisher",
                    partial(_publish_cycle, database, effective_adapters),
                )
                task_group.start_soon(
                    _worker_loop,
                    "notification-dispatcher",
                    partial(_dispatch_cycle, database, effective_adapters),
                )
                task_group.start_soon(
                    _worker_loop,
                    "notification-recovery",
                    partial(_recover_notifications, database, effective_adapters),
                )
                task_group.start_soon(
                    _worker_loop,
                    "conversation-agent",
                    partial(_agent_cycle, database, effective_adapters, settings),
                )
                task_group.start_soon(
                    _worker_loop,
                    "knowledge-index",
                    partial(_knowledge_cycle, database, effective_adapters),
                )
                yield {"database": database, "project_root": project_root}
                task_group.cancel_scope.cancel()
        finally:
            runtime_to_close = qq_runtime
            qq_runtime = None
            await _cleanup_lifecycle_resources(runtime_to_close, owned, database)

    routes = [
        Route("/health", health, methods=["GET"]),
        Route(settings.qq_webhook_path, qq_webhook, methods=["POST"]),
    ]
    return Starlette(routes=routes, lifespan=lifespan)
