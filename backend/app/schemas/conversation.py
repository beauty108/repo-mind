"""
Pydantic schemas for Conversation endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Request body for POST /api/repos/{id}/chat."""
    message: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="The user's question about the repository.",
        examples=["How does dependency injection work?"],
    )
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Optional: pass an existing conversation ID to continue a conversation. "
            "If omitted, a new conversation is created."
        ),
    )


class ConversationResponse(BaseModel):
    """Metadata for a conversation."""
    id: uuid.UUID
    repository_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}
