"""
Pydantic schemas for Message endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class CitationSchema(BaseModel):
    """A structured citation linking to a code chunk."""
    file_path: str
    start_line: int
    end_line: int
    content: str | None = None
    symbol_name: str | None = None
    symbol_type: str | None = None
    language: str | None = None
    similarity: float | None = None


class MessageResponse(BaseModel):
    """One message in a conversation."""
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    citations: list[CitationSchema] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationMessagesResponse(BaseModel):
    """Full message history for a conversation."""
    conversation_id: uuid.UUID
    repository_id: uuid.UUID
    messages: list[MessageResponse]
