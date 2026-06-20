"""
Conversation ORM model — scoped to a repository, not a user (no auth).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    repository: Mapped["Repository"] = relationship(  # noqa: F821
        "Repository", back_populates="conversations"
    )
    messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        "Message", back_populates="conversation", cascade="all, delete-orphan",
        order_by="Message.created_at"
    )
