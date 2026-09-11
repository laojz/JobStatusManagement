"""Identity input and output schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BootstrapIdentity(BaseModel):
    """Validated identity data crossing the CLI/configuration boundary."""

    model_config = ConfigDict(frozen=True)

    user_external_key: str = Field(min_length=1)
    user_display_name: str = Field(min_length=1)
    mail_provider: str = Field(min_length=1)
    mail_account_key: str = Field(min_length=1)
    mail_display_name: str = Field(min_length=1)
    mail_credential_ref: str | None = None


class IdentitySummary(BaseModel):
    """Safe bootstrap result without credential material."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    mail_account_id: str
    created: bool
    created_at: datetime
