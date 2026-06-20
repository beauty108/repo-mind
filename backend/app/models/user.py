"""
User ORM model.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional: GitHub PAT stored encrypted (null if not set)
    # In a production system this would be AES-encrypted. For now it's stored as-is
    # and users are advised to use repo-scoped read-only tokens.
    github_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    repositories: Mapped[list["Repository"]] = relationship(  # noqa: F821
        "Repository", back_populates="owner_user", cascade="all, delete-orphan"
    )
