"""QQ webhook boundary and durable intake."""

from __future__ import annotations

import binascii
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse, Response

from jobs_status_manager.agent.contracts import AgentRunState, QQInboundEvent
from jobs_status_manager.agent.models import (
    AgentRun,
    ConversationMessage,
    QQInboundIdentity,
    Session,
)
from jobs_status_manager.agent.uploads import discard_upload, prepare_upload
from jobs_status_manager.application_core.router import route_command
from jobs_status_manager.identity.models import User
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.knowledge.contracts import UserFileState
from jobs_status_manager.knowledge.files import UnsupportedUploadError
from jobs_status_manager.knowledge.models import UserFile

if TYPE_CHECKING:
    from starlette.requests import Request

    from jobs_status_manager.application.lifecycle import LifecycleAdapters
    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator


@dataclass(frozen=True, slots=True)
class WebhookContext:
    """Dependencies needed to persist a validated event."""

    database: Database
    settings: AppSettings
    clock: Clock
    ids: IdGenerator


def _parse_event(adapters: LifecycleAdapters, body: bytes) -> QQInboundEvent | None:
    if adapters.qq is None:
        return None
    try:
        return adapters.qq.receive(body)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _validate_event(
    request: Request,
    context: WebhookContext,
    adapters: LifecycleAdapters,
    body: bytes,
) -> QQInboundEvent | Response:
    qq = adapters.qq
    if qq is None or context.settings.qq_webhook_token is None:
        return _response(503, "webhook unavailable")
    token = request.headers.get("x-qq-webhook-token")
    if not qq.validate_webhook(token, body):
        return _response(401, "invalid webhook token")
    event = _parse_event(adapters, body)
    if event is None:
        return _response(400, "invalid event")
    if event.event_type not in {"C2C_MESSAGE_CREATE", "C2C_FILE_CREATE"}:
        return JSONResponse({"status": "ignored"}, status_code=202)
    if (
        context.settings.qq_user_openid is not None
        and event.user_openid != context.settings.qq_user_openid
    ):
        return _response(403, "unknown sender")
    return event


def _persist_event(
    context: WebhookContext,
    event: QQInboundEvent,
    *,
    command_run: bool = False,
) -> Response:
    if _has_identity(context, event):
        return JSONResponse({"status": "duplicate"}, status_code=200)
    upload = prepare_upload(context.settings, context.ids, event)
    persisted = False
    try:
        with transaction(context.database) as session:
            existing = session.scalar(
                select(QQInboundIdentity).where(
                    and_(
                        QQInboundIdentity.identity_type == "event",
                        QQInboundIdentity.identity_value == event.event_id,
                    )
                    | and_(
                        QQInboundIdentity.identity_type == "message",
                        QQInboundIdentity.identity_value == event.message_id,
                    )
                )
            )
            if existing is not None:
                return JSONResponse({"status": "duplicate"}, status_code=200)
            user_id = session.scalar(
                select(User.id).where(
                    User.external_key == context.settings.bootstrap_user_external_key
                )
            )
            if user_id is None:
                return _response(403, "user unavailable")
            now = context.clock.now()
            session_row = session.scalar(
                select(Session).where(Session.user_id == user_id, Session.session_type == "MAIN")
            )
            if session_row is None:
                session_row = Session(
                    id=str(context.ids.new_id()),
                    user_id=user_id,
                    session_type="MAIN",
                    summary="",
                    created_at=now,
                    last_active_at=now,
                    updated_at=now,
                )
                session.add(session_row)
                session.flush()
            else:
                session_row.updated_at = now
                session.flush()
            message = ConversationMessage(
                id=str(context.ids.new_id()),
                session_id=session_row.id,
                role="user",
                content=event.content,
                provider_event_id=event.event_id,
                provider_name="qq",
                provider_scope="c2c",
                provider_target_id=event.user_openid,
                provider_message_id=event.message_id,
                provider_msg_seq=event.msg_seq,
                created_at=now,
            )
            session.add(message)
            session.flush()
            if upload is not None:
                session.add(
                    UserFile(
                        id=upload.file_id,
                        user_id=user_id,
                        provider_file_id=upload.metadata.provider_file_id,
                        filename=upload.metadata.filename,
                        content_type=upload.metadata.content_type,
                        size_bytes=upload.metadata.size_bytes,
                        storage_path=upload.path,
                        state=UserFileState.STORED.value,
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.add_all(
                [
                    QQInboundIdentity(
                        id=str(context.ids.new_id()),
                        message_id=message.id,
                        identity_type="event",
                        identity_value=event.event_id,
                        created_at=now,
                    ),
                    QQInboundIdentity(
                        id=str(context.ids.new_id()),
                        message_id=message.id,
                        identity_type="message",
                        identity_value=event.message_id,
                        created_at=now,
                    ),
                ]
            )
            is_active = (
                session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.session_id == session_row.id,
                        AgentRun.state.in_(
                            (
                                AgentRunState.RUNNING.value,
                                AgentRunState.QUEUED.value,
                                AgentRunState.DELIVERY_PENDING.value,
                                AgentRunState.WAITING_USER_CONFIRMATION.value,
                            )
                        ),
                    )
                )
                is not None
            )
            run = AgentRun(
                id=str(context.ids.new_id()),
                session_id=session_row.id,
                user_message_id=message.id,
                state=(
                    AgentRunState.RUNNING.value
                    if command_run or not is_active
                    else AgentRunState.QUEUED.value
                ),
                error=None,
                created_at=now,
                started_at=now if command_run or not is_active else None,
                attempt_count=1 if command_run or not is_active else 0,
                next_retry_at=None,
                completed_at=None,
            )
            session.add(run)
            session.flush()
            if not is_active and not command_run:
                session_row.active_run_id = run.id
            session_row.last_active_at = now
            session_row.updated_at = now
        persisted = True
    except IntegrityError:
        if _has_identity(context, event):
            return JSONResponse({"status": "duplicate"}, status_code=200)
        raise
    finally:
        if upload is not None and not persisted:
            discard_upload(upload)
    return JSONResponse({"status": "accepted"}, status_code=202)


async def receive_webhook(
    request: Request,
    context: WebhookContext,
    adapters: LifecycleAdapters,
) -> Response:
    """Validate and durably enqueue one supported QQ text event."""
    body = await request.body()
    event = _validate_event(request, context, adapters, body)
    if isinstance(event, Response):
        return event
    try:
        return _persist_event(
            context,
            event,
            command_run=route_command(event.content).resolution is not None,
        )
    except (UnsupportedUploadError, binascii.Error):
        return _response(400, "invalid file upload")


def _has_identity(context: WebhookContext, event: QQInboundEvent) -> bool:
    with transaction(context.database) as session:
        return (
            session.scalar(
                select(QQInboundIdentity).where(
                    and_(
                        QQInboundIdentity.identity_type == "event",
                        QQInboundIdentity.identity_value == event.event_id,
                    )
                    | and_(
                        QQInboundIdentity.identity_type == "message",
                        QQInboundIdentity.identity_value == event.message_id,
                    )
                )
            )
            is not None
        )


def _response(status_code: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status_code)
