"""Create Phase 3 conversation agent tables."""

import sqlalchemy as sa
from alembic import op

revision = "0005_conversation_agent"
down_revision = "0004_mail_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create durable sessions, transcript, runs, calls, and results."""
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_type", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("active_application_id", sa.String(36), nullable=True),
        sa.Column("active_mail_id", sa.String(36), nullable=True),
        sa.Column("active_run_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "session_type"),
    )
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_event_id"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column(
            "user_message_id",
            sa.String(36),
            sa.ForeignKey("conversation_messages.id"),
            nullable=False,
        ),
        sa.Column(
            "final_message_id",
            sa.String(36),
            sa.ForeignKey("conversation_messages.id"),
            nullable=True,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("delivery_state", sa.String(32), nullable=True),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_message_id"),
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_run_id", "sequence"),
    )
    op.create_table(
        "tool_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tool_call_id", sa.String(36), sa.ForeignKey("tool_calls.id"), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("context_refs", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tool_call_id"),
    )
    op.create_table(
        "qq_inbound_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("conversation_messages.id"),
            nullable=False,
        ),
        sa.Column("identity_type", sa.String(32), nullable=False),
        sa.Column("identity_value", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("identity_type", "identity_value"),
    )


def downgrade() -> None:
    """Drop Phase 3 conversation tables."""
    op.drop_table("qq_inbound_identities")
    op.drop_table("tool_results")
    op.drop_table("tool_calls")
    op.drop_table("agent_runs")
    op.drop_table("conversation_messages")
    op.drop_table("sessions")
