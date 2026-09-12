"""Persist provider-neutral QQ passive reply metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0008_qq_reply_targets"
down_revision = "0007_phase6_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable provider metadata to inbound conversation messages."""
    op.add_column("conversation_messages", sa.Column("provider_name", sa.String(64)))
    op.add_column("conversation_messages", sa.Column("provider_scope", sa.String(64)))
    op.add_column("conversation_messages", sa.Column("provider_target_id", sa.String(255)))
    op.add_column("conversation_messages", sa.Column("provider_message_id", sa.String(255)))
    op.add_column("conversation_messages", sa.Column("provider_msg_seq", sa.Integer()))


def downgrade() -> None:
    """Remove QQ passive reply metadata while retaining existing event IDs."""
    op.drop_column("conversation_messages", "provider_msg_seq")
    op.drop_column("conversation_messages", "provider_message_id")
    op.drop_column("conversation_messages", "provider_target_id")
    op.drop_column("conversation_messages", "provider_scope")
    op.drop_column("conversation_messages", "provider_name")
