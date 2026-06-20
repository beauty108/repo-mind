"""
Repository ORM model.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Imported to make sure these models are in the SQLAlchemy class registry
from app.models.file import File  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.conversation import Conversation  # noqa: F401


class RepositoryStatus(str, enum.Enum):
    pending = "pending"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    github_url: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[RepositoryStatus] = mapped_column(
        Enum(RepositoryStatus, name="repositorystatus"),
        nullable=False,
        default=RepositoryStatus.pending,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # SHA of the HEAD commit at index time; used to skip re-indexing unchanged repos
    indexed_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Name of the embedding model used when this repo was indexed
    embedding_model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Private repository support
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Multi-tenancy: nullable so existing repos without an owner still work
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    files: Mapped[list["File"]] = relationship(  # noqa: F821
        "File", back_populates="repository", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(  # noqa: F821
        "Conversation", back_populates="repository", cascade="all, delete-orphan"
    )
    owner_user: Mapped["User | None"] = relationship(  # noqa: F821
        "User", back_populates="repositories"
    )
