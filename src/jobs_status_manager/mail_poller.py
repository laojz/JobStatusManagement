"""Synchronous IMAP poll cycles with durable cursor advancement."""

from dataclasses import dataclass

from jobs_status_manager.identity.models import MailAccount
from jobs_status_manager.infrastructure.adapters.protocols import IMAPGateway
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.mail_service import MailServices, ingest_mail


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
    try:
        envelopes = gateway.poll(account_key, cursor)
    except RuntimeError as error:
        with transaction(services.database) as session:
            account = session.get(MailAccount, account_id)
            if account is not None:
                account.polling_attempt_count += 1
                account.polling_last_error = safe_external_error(error)
                account.last_polled_at = services.clock.now()
        return PollResult(ingested=0, failed=True)
    ingested = sum(
        ingest_mail(services, (user_id, account_id), envelope).created for envelope in envelopes
    )
    with transaction(services.database) as session:
        account = session.get(MailAccount, account_id)
        if account is not None:
            account.polling_attempt_count += 1
            account.polling_last_error = None
            account.last_polled_at = services.clock.now()
            if envelopes:
                account.polling_cursor = envelopes[-1].provider_message_id
    return PollResult(ingested=ingested, failed=False)
