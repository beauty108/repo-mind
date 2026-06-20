"""
Embedding ORM model — stores pgvector embeddings for each CodeChunk.
One-to-one with CodeChunk.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# BGE-small-en-v1.5 produces 384-dimensional vectors.
# OpenAI text-embedding-3-small produces 1536-dimensional vectors.
# The vector column dimension is set to 384 (the local/default provider).
# Switching providers requires re-indexing — model_name is validated at query time.
VECTOR_DIMENSION = 384


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("code_chunks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,   # One embedding per chunk
        index=True,
    )
    # pgvector column — cosine similarity search via <=> operator
    vector: Mapped[list[float]] = mapped_column(
        Vector(VECTOR_DIMENSION), nullable=False
    )
    # Name of the embedding model used to produce this vector.
    # Must match the active EmbeddingClient's model at query time.
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    chunk: Mapped["CodeChunk"] = relationship(  # noqa: F821
        "CodeChunk", back_populates="embedding"
    )
