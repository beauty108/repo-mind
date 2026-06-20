"""
File ORM model — one row per source file indexed in a repository.
"""

import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class File(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(
        String(1024), nullable=False, comment="Relative path from repo root"
    )
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Relationships
    repository: Mapped["Repository"] = relationship(  # noqa: F821
        "Repository", back_populates="files"
    )
    chunks: Mapped[list["CodeChunk"]] = relationship(  # noqa: F821
        "CodeChunk", back_populates="file", cascade="all, delete-orphan"
    )
