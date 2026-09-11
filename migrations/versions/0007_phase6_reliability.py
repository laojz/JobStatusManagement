"""Persist Phase 6 execution leases and retry scan indexes."""

import sqlalchemy as sa
from alembic import op

revision = "0007_phase6_reliability"
down_revision = "0006_phase5_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add durable AgentRun execution timing and retry scan indexes."""
    op.add_column("agent_runs", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column(
        "agent_runs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_runs", sa.Column("next_retry_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE agent_runs SET started_at = created_at WHERE started_at IS NULL")
    op.create_index(
        "ix_pending_actions_retry_scan",
        "pending_actions",
        ["action_type", "state", "next_retry_at"],
    )
    op.create_index(
        "ix_pending_actions_stale_scan",
        "pending_actions",
        ["action_type", "state", "execution_started_at"],
    )
    op.create_index("ix_agent_runs_stale_scan", "agent_runs", ["state", "started_at"])
    op.create_index("ix_agent_runs_retry_scan", "agent_runs", ["state", "next_retry_at"])


def downgrade() -> None:
    """Remove Phase 6 execution timing and retry scan indexes."""
    op.drop_index("ix_agent_runs_retry_scan", table_name="agent_runs")
    op.drop_index("ix_agent_runs_stale_scan", table_name="agent_runs")
    op.drop_index("ix_pending_actions_stale_scan", table_name="pending_actions")
    op.drop_index("ix_pending_actions_retry_scan", table_name="pending_actions")
    op.drop_column("agent_runs", "next_retry_at")
    op.drop_column("agent_runs", "attempt_count")
    op.drop_column("agent_runs", "started_at")
