"""Persist OpenAI-compatible provider tool-call continuation metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0009_tool_call_provider_metadata"
down_revision = "0008_qq_reply_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable provider metadata while preserving legacy ToolCall rows."""
    op.add_column("tool_calls", sa.Column("provider_call_id", sa.String(255)))
    op.add_column("tool_calls", sa.Column("provider_type", sa.String(32)))
    op.add_column("tool_calls", sa.Column("provider_arguments_json", sa.Text()))
    op.add_column("tool_calls", sa.Column("assistant_sequence", sa.Integer()))


def downgrade() -> None:
    """Remove provider continuation metadata without changing internal calls."""
    op.drop_column("tool_calls", "assistant_sequence")
    op.drop_column("tool_calls", "provider_arguments_json")
    op.drop_column("tool_calls", "provider_type")
    op.drop_column("tool_calls", "provider_call_id")
