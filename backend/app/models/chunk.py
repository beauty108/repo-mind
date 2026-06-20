"""
CodeChunk ORM model — one row per semantic chunk (function, class, or module block).
"""

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SymbolType(str, enum.Enum):
    function = "function"
    cls = "class"
    module = "module"


class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # For function/class chunks: the name of the symbol; None for module-level chunks
    symbol_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    symbol_type: Mapped[SymbolType] = mapped_column(
        Enum(SymbolType, name="symboltype", values_callable=lambda obj: [e.value for e in obj]), nullable=False
    )

    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    file: Mapped["File"] = relationship("File", back_populates="chunks")  # noqa: F821
    embedding: Mapped["Embedding"] = relationship(  # noqa: F821
        "Embedding", back_populates="chunk", uselist=False, cascade="all, delete-orphan"
    )
