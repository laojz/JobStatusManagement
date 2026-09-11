"""Add future-runtime context references to PendingAction."""

import sqlalchemy as sa
from alembic import op

revision = "0003_pending_action_context_refs"
down_revision = "0002_application_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable references without introducing future phase tables."""
    for column in ("session_id", "agent_run_id", "tool_call_id", "mail_analysis_id"):
        op.add_column("pending_actions", sa.Column(column, sa.String(length=36), nullable=True))


def downgrade() -> None:
    """Remove future-runtime context references."""
    for column in ("mail_analysis_id", "tool_call_id", "agent_run_id", "session_id"):
        op.drop_column("pending_actions", column)
