"""Create Phase 5 file and knowledge tables."""

import sqlalchemy as sa
from alembic import op

revision = "0006_phase5_rag"
down_revision = "0005_conversation_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create relational knowledge source-of-truth tables."""
    op.add_column("sessions", sa.Column("active_knowledge_document_id", sa.String(36)))
    op.create_table(
        "user_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider_file_id", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(127), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.Text()),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider_file_id"),
    )
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_file_id", sa.String(36), sa.ForeignKey("user_files.id")),
        sa.Column(
            "pending_action_id",
            sa.String(36),
            sa.ForeignKey("pending_actions.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("company", sa.String(255)),
        sa.Column("department", sa.String(255)),
        sa.Column("position", sa.String(255)),
        sa.Column("knowledge_domain", sa.String(255)),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("embedding_version", sa.String(64), nullable=False),
        sa.Column("lifecycle_status", sa.String(16), nullable=False),
        sa.Column("index_status", sa.String(16), nullable=False),
        sa.Column("index_attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_index_error", sa.Text()),
        sa.Column("index_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pending_action_id"),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("knowledge_documents.id"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("section_title", sa.String(255)),
        sa.Column("token_count", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index"),
    )


def downgrade() -> None:
    """Drop Phase 5 knowledge tables."""
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("user_files")
    op.drop_column("sessions", "active_knowledge_document_id")
