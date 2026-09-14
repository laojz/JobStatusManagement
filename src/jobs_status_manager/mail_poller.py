"""Synchronous IMAP poll cycles with durable cursor advancement."""

import time
from dataclasses import dataclass
from typing import TypedDict

import structlog

from jobs_status_manager.identity.models import MailAccount
from jobs_status_manager.infrastructure.adapters.protocols import IMAPGateway
from jobs_status_manager.infrastructure.adapters.qq_imap_cursor import (
    IMAPCursorError,
    IMAPError,
    decode_cursor,
)
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.mail_service import MailServices, ingest_mail

logger = structlog.get_logger(__name__)


class CursorLogFields(TypedDict, total=False):
    """Safe numeric cursor fields for structured poll events."""

    cursor_uidvalidity: int
    cursor_uid: int


def _cursor_log_fields(cursor: str) -> CursorLogFields:
    try:
        decoded = decode_cursor(cursor)
    except IMAPCursorError:
        return {}
    return {"cursor_uidvalidity": decoded.uidvalidity, "cursor_uid": decoded.uid}


@dataclass(frozen=True, slots=True)
class PollResult:
    """Observable result of one account poll."""

    ingested: int
    failed: bool


def poll_once(services: MailServices, account_id: str, gateway: IMAPGateway) -> PollResult:
    """Poll outside transactions and advance cursor only after durable ingestion."""
    with transaction(services.database) as session:
        account = session.get(MailAccount, account_id)
        if account is None or not account.is_active:
            return PollResult(ingested=0, failed=False)
        account_key = account.account_key
        user_id = account.user_id
        cursor = account.polling_cursor
    started_at = time.monotonic()
    try:
        batch = gateway.poll(account_key, cursor)
    except (RuntimeError, IMAPError) as error:
        safe_error = safe_external_error(error)
        with transaction(services.database) as session:
            account = session.get(MailAccount, account_id)
            if account is not None:
                account.polling_attempt_count += 1
                account.polling_last_error = safe_error
                account.last_polled_at = services.clock.now()
        logger.warning(
            "imap_poll_failed",
            component="imap_poller",
            account_id=account_id,
            outcome="failure",
            phase=error.phase.value
            if isinstance(error, IMAPError) and error.phase is not None
            else None,
            error_type=type(error).__name__,
            error=safe_error,
            cursor_present=cursor is not None,
            duration_ms=round((time.monotonic() - started_at) * 1000, 3),
            **_cursor_log_fields(cursor or ""),
        )
        return PollResult(ingested=0, failed=True)
    ingested = sum(
        ingest_mail(services, (user_id, account_id), envelope).created
        for envelope in batch.envelopes
    )
    with transaction(services.database) as session:
        account = session.get(MailAccount, account_id)
        if account is not None:
            account.polling_attempt_count += 1
            account.polling_last_error = None
            account.last_polled_at = services.clock.now()
            account.polling_cursor = batch.next_cursor
    event = "imap_cursor_reset" if batch.reset else "imap_poll_succeeded"
    log = logger.info if batch.reset else logger.debug
    outcome = "reset" if batch.reset else ("empty" if not batch.envelopes else "success")
    log(
        event,
        component="imap_poller",
        account_id=account_id,
        outcome=outcome,
        fetched_count=len(batch.envelopes),
        ingested_count=ingested,
        duration_ms=round((time.monotonic() - started_at) * 1000, 3),
        **_cursor_log_fields(batch.next_cursor),
    )
    return PollResult(ingested=ingested, failed=False)
