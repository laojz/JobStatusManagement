"""Create Phase 2 mail pipeline tables and recovery fields."""

import sqlalchemy as sa
from alembic import op

revision = "0004_mail_pipeline"
down_revision = "0003_pending_action_context_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create durable mail, analysis, event-consumer, and notification state."""
    with op.batch_alter_table("mail_accounts") as batch:
        batch.add_column(sa.Column("polling_cursor", sa.String(255), nullable=True))
        batch.add_column(sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("polling_attempt_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("polling_last_error", sa.Text(), nullable=True))
    op.create_table(
        "mails",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "mail_account_id", sa.String(36), sa.ForeignKey("mail_accounts.id"), nullable=False
        ),
        sa.Column("provider_message_id", sa.String(255), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("processing_state", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mail_account_id", "provider_message_id"),
    )
    op.create_table(
        "job_mail_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mail_id", sa.String(36), sa.ForeignKey("mails.id"), nullable=False),
        sa.Column("mail_type", sa.String(32), nullable=False),
        sa.Column("application", sa.JSON(), nullable=False),
        sa.Column("status_suggestion", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.JSON(), nullable=False),
        sa.Column("analysis_version", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mail_id"),
    )
    op.create_table(
        "processed_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("consumer_name", sa.String(100), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("consumer_name", "event_id"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.String(36), nullable=False),
        sa.Column("related_application_id", sa.String(36), nullable=True),
        sa.Column("related_mail_id", sa.String(36), sa.ForeignKey("mails.id"), nullable=True),
        sa.Column(
            "related_pending_action_id",
            sa.String(36),
            sa.ForeignKey("pending_actions.id"),
            nullable=True,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_event_id", "type"),
    )
    op.create_table(
        "notification_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "notification_id", sa.String(36), sa.ForeignKey("notifications.id"), nullable=False
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("notification_id", "attempt_no"),
    )


def downgrade() -> None:
    """Drop Phase 2 durable state."""
    op.drop_table("notification_attempts")
    op.drop_table("notifications")
    op.drop_table("processed_events")
    op.drop_table("job_mail_analyses")
    op.drop_table("mails")
    with op.batch_alter_table("mail_accounts") as batch:
        for column in (
            "polling_last_error",
            "polling_attempt_count",
            "last_polled_at",
            "polling_cursor",
        ):
            batch.drop_column(column)
