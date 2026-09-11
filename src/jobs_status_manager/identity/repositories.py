"""Identity repositories."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobs_status_manager.identity.models import MailAccount, User


def find_user(session: Session, external_key: str) -> User | None:
    """Find a user by its stable external key."""
    return session.scalar(select(User).where(User.external_key == external_key))


def find_mail_account(
    session: Session, user_id: str, provider: str, account_key: str
) -> MailAccount | None:
    """Find an account by its idempotent identity key."""
    return session.scalar(
        select(MailAccount).where(
            MailAccount.user_id == user_id,
            MailAccount.provider == provider,
            MailAccount.account_key == account_key,
        )
    )


def create_user(
    session: Session, user_id: str, external_key: str, display_name: str, now: datetime
) -> User:
    """Create a user identity."""
    user = User(
        id=user_id,
        external_key=external_key,
        display_name=display_name,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    return user


def create_mail_account(  # noqa: PLR0913
    session: Session,
    *,
    account_id: str,
    user_id: str,
    provider: str,
    account_key: str,
    display_name: str,
    credential_ref: str | None,
    now: datetime,
) -> MailAccount:
    """Create a mailbox identity without storing plaintext secrets."""
    account = MailAccount(
        id=account_id,
        user_id=user_id,
        provider=provider,
        account_key=account_key,
        display_name=display_name,
        credential_ref=credential_ref,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    return account
