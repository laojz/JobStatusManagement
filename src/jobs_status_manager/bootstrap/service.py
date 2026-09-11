"""Idempotent Phase 0 identity bootstrap."""

from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.identity.repositories import (
    create_mail_account,
    create_user,
    find_mail_account,
    find_user,
)
from jobs_status_manager.identity.schemas import BootstrapIdentity, IdentitySummary
from jobs_status_manager.infrastructure.clock import Clock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import IdGenerator


class BootstrapConflictError(Exception):
    """Raised when existing identity data conflicts with requested bootstrap data."""

    def __init__(self, entity: str, field: str) -> None:
        """Capture the conflicting safe field identity."""
        super().__init__()
        self.entity = entity
        self.field = field

    def __str__(self) -> str:  # noqa: D105
        return f"bootstrap conflict: {self.entity}.{self.field}"


def bootstrap_identity(
    database: Database,
    settings: AppSettings,
    clock: Clock,
    ids: IdGenerator,
) -> IdentitySummary:
    """Create or reuse the configured user and mailbox identity."""
    identity = BootstrapIdentity(
        user_external_key=settings.bootstrap_user_external_key,
        user_display_name=settings.bootstrap_user_display_name,
        mail_provider=settings.bootstrap_mail_provider,
        mail_account_key=settings.bootstrap_mail_account_key,
        mail_display_name=settings.bootstrap_mail_display_name,
        mail_credential_ref=settings.bootstrap_mail_credential_ref,
    )
    now = clock.now()
    with transaction(database) as session:
        user = find_user(session, identity.user_external_key)
        created = user is None
        if user is None:
            user = create_user(
                session,
                str(ids.new_id()),
                identity.user_external_key,
                identity.user_display_name,
                now,
            )
        elif user.display_name != identity.user_display_name:
            entity = "user"
            field = "display_name"
            raise BootstrapConflictError(entity, field)

        account = find_mail_account(
            session, user.id, identity.mail_provider, identity.mail_account_key
        )
        if account is None:
            account = create_mail_account(
                session,
                account_id=str(ids.new_id()),
                user_id=user.id,
                provider=identity.mail_provider,
                account_key=identity.mail_account_key,
                display_name=identity.mail_display_name,
                credential_ref=identity.mail_credential_ref,
                now=now,
            )
        elif account.display_name != identity.mail_display_name:
            entity = "mail_account"
            field = "display_name"
            raise BootstrapConflictError(entity, field)
        elif account.credential_ref != identity.mail_credential_ref:
            entity = "mail_account"
            field = "credential_ref"
            raise BootstrapConflictError(entity, field)

        return IdentitySummary(
            user_id=user.id,
            mail_account_id=account.id,
            created=created,
            created_at=user.created_at,
        )
