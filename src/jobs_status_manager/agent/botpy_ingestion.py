"""Durable botpy callback adaptation."""

import binascii
import json
from collections.abc import Mapping
from functools import partial

import anyio
from sqlalchemy.exc import IntegrityError

from jobs_status_manager.agent.webhook import WebhookContext, _persist_event
from jobs_status_manager.application_core.router import route_command
from jobs_status_manager.infrastructure.qq_botpy import DurableEventHandler, DurableIngestionReceipt
from jobs_status_manager.infrastructure.qq_botpy_events import (
    _has_multiple_attachments,
    is_valid_unsupported_event,
    normalize_sdk_event,
)
from jobs_status_manager.knowledge.files import UnsupportedUploadError


async def receive_botpy_event(
    body: bytes,
    context: WebhookContext,
) -> tuple[bool, int, bytes]:
    """Normalize one botpy payload and persist it before an SDK ACK."""
    result: tuple[bool, int, bytes]
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        result = _error(400, "invalid event")
    else:
        event = normalize_sdk_event(payload) if isinstance(payload, dict) else None
        if event is None:
            result = (
                _error(422, "multiple attachments are unsupported")
                if isinstance(payload, dict) and _has_multiple_attachments(payload)
                else (
                    (
                        True,
                        202,
                        b'{"status":"ignored"}',
                    )
                    if isinstance(payload, dict) and is_valid_unsupported_event(payload)
                    else _error(400, "invalid event")
                )
            )
        elif event.user_openid != context.settings.qq_user_openid:
            result = _error(403, "unknown sender")
        else:
            try:
                response = await anyio.to_thread.run_sync(
                    partial(
                        _persist_event,
                        context,
                        event,
                        command_run=route_command(event.content).resolution is not None,
                    )
                )
            except (UnsupportedUploadError, binascii.Error):
                result = _error(400, "invalid file upload")
            except (IntegrityError, OSError, RuntimeError):
                result = _error(503, "persistence failed")
            else:
                result = (
                    response.status_code in {200, 202},
                    response.status_code,
                    bytes(response.body),
                )
    return result


def create_botpy_event_handler(context: WebhookContext) -> DurableEventHandler:
    """Bind durable application context to the transport callback contract."""

    async def handle(body: bytes, _: Mapping[str, str]) -> DurableIngestionReceipt:
        accepted, status_code, response_body = await receive_botpy_event(body, context)
        return DurableIngestionReceipt(accepted, status_code, response_body)

    return handle


def _error(status_code: int, message: str) -> tuple[bool, int, bytes]:
    return False, status_code, json.dumps({"error": message}).encode("utf-8")
