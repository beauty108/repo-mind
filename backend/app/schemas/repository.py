"""
Pydantic schemas for Repository endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, HttpUrl


class RepoSubmitRequest(BaseModel):
    """Request body for POST /api/repos."""
    github_url: str = Field(
        ...,
        description="GitHub repository URL (https://github.com/owner/repo). Public or private.",
        examples=["https://github.com/fastapi/fastapi"],
    )
    github_token: str | None = Field(
        default=None,
        description=(
            "Optional GitHub Personal Access Token for private repositories. "
            "Never stored server-side — only used during the clone operation. "
            "Use a repo-scoped read-only token for security."
        ),
    )

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        """Pre-validate format before hitting the cloner."""
        v = v.strip()
        if not v.startswith("https://github.com/"):
            raise ValueError(
                "Only GitHub URLs are accepted (https://github.com/owner/repo)"
            )
        return v


class RepoResponse(BaseModel):
    """Response for POST /api/repos and GET /api/repos/{id}."""
    id: uuid.UUID
    github_url: str
    owner: str
    repo_name: str
    status: Literal["pending", "indexing", "ready", "failed"]
    error_message: str | None = None
    indexed_file_count: int = 0
    skipped_file_count: int = 0
    indexed_commit_sha: str | None = None
    embedding_model_name: str | None = None
    is_private: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FileResponse(BaseModel):
    """One file entry for GET /api/repos/{id}/files."""
    id: uuid.UUID
    path: str
    language: str
    size_bytes: int

    model_config = {"from_attributes": True}


class FileListResponse(BaseModel):
    """Paginated file list response."""
    items: list[FileResponse]
    total: int
    page: int
    page_size: int
