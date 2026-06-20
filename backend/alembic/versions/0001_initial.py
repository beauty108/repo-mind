"""Initial schema — all tables + pgvector extension + IVFFlat index.

Revision ID: 0001
Revises: (none)
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

VECTOR_DIM = 384  # BAAI/bge-small-en-v1.5


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Enums are created automatically by SQLAlchemy when op.create_table() runs
    # (via sa.Enum with create_type=True, which is the default).
    # We use IF NOT EXISTS at the SQL level via checkfirst on the sa.Enum itself.


    # repositories
    op.create_table(
        "repositories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("github_url", sa.String(512), nullable=False, unique=True),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("repo_name", sa.String(255), nullable=False),
        sa.Column("status", sa.Enum("pending", "indexing", "ready", "failed", name="repositorystatus", create_type=True), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("indexed_file_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_file_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("indexed_commit_sha", sa.String(40), nullable=True),
        sa.Column("embedding_model_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_repositories_github_url", "repositories", ["github_url"])

    # files
    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.create_index("ix_files_repository_id", "files", ["repository_id"])

    # code_chunks
    op.create_table(
        "code_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("symbol_name", sa.String(512), nullable=True),
        sa.Column("symbol_type", sa.Enum("function", "class", "module", name="symboltype", create_type=True), nullable=False),
        sa.Column("start_line", sa.Integer, nullable=False),
        sa.Column("end_line", sa.Integer, nullable=False),
    )
    op.create_index("ix_code_chunks_file_id", "code_chunks", ["file_id"])

    # embeddings
    op.create_table(
        "embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("code_chunks.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("vector", Vector(VECTOR_DIM), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_embeddings_chunk_id", "embeddings", ["chunk_id"])

    # IVFFlat index for cosine similarity on vector column.
    # lists=100 is suitable for up to ~1M vectors; tune as dataset grows.
    # NOTE: This index requires data to be present to be useful; for new DBs
    # it may be built lazily. Alternatively use HNSW for better recall.
    op.execute(
        f"CREATE INDEX ix_embeddings_vector ON embeddings "
        f"USING ivfflat (vector vector_cosine_ops) WITH (lists = 100)"
    )

    # conversations
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_conversations_repository_id", "conversations", ["repository_id"])

    # messages
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Enum("user", "assistant", name="messagerole", create_type=True), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("citations", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("embeddings")
    op.drop_table("code_chunks")
    op.drop_table("files")
    op.drop_table("repositories")

    op.execute("DROP TYPE IF EXISTS messagerole")
    op.execute("DROP TYPE IF EXISTS symboltype")
    op.execute("DROP TYPE IF EXISTS repositorystatus")
    op.execute("DROP EXTENSION IF EXISTS vector")
