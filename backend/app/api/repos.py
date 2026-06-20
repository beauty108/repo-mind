"""
Repository API endpoints.

POST   /api/repos              — submit a GitHub URL for indexing
GET    /api/repos/{id}         — get repository status and metadata
GET    /api/repos/{id}/files   — paginated list of indexed files
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_optional_user
from app.models.file import File
from app.models.repository import Repository, RepositoryStatus
from app.schemas.repository import (
    FileListResponse,
    FileResponse,
    RepoResponse,
    RepoSubmitRequest,
)
from app.worker.cloner import validate_github_url, ClonerError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos", tags=["repositories"])


def _get_limiter():
    """Lazy import to avoid circular dependency at module load time."""
    from app.main import limiter
    return limiter


@router.post(
    "",
    response_model=RepoResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a GitHub repository for indexing",
    description=(
        "Submit a public GitHub repository URL. The repository will be cloned "
        "and indexed in the background. Poll GET /api/repos/{id} for status."
    ),
)
async def submit_repo(
    request: Request,
    body: RepoSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    # Validate URL
    try:
        owner, repo_name = validate_github_url(body.github_url)
    except ClonerError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    # Resolve the GitHub token to use
    github_token = body.github_token
    if not github_token and current_user and current_user.github_access_token:
        github_token = current_user.github_access_token

    # Normalize URL (strip trailing .git, trailing slash)
    canonical_url = f"https://github.com/{owner}/{repo_name}"

    # Check if already exists
    result = await db.execute(
        select(Repository).where(Repository.github_url == canonical_url)
    )
    existing = result.scalar_one_or_none()

    if existing:
        if existing.status == RepositoryStatus.ready:
            # Re-index: enqueue a new job (the task will check SHA and possibly skip)
            logger.info(
                "Re-index requested for existing repo",
                extra={"repo_id": str(existing.id), "github_url": canonical_url},
            )
            from app.worker.tasks import clone_and_index
            clone_and_index.delay(str(existing.id), github_token)
            existing.status = RepositoryStatus.pending
            await db.commit()
            await db.refresh(existing)
            return RepoResponse.model_validate(existing)

        elif existing.status in (RepositoryStatus.pending, RepositoryStatus.indexing):
            # Already queued — return current status
            return RepoResponse.model_validate(existing)

        elif existing.status == RepositoryStatus.failed:
            # Retry after failure
            existing.status = RepositoryStatus.pending
            existing.error_message = None
            if github_token:
                existing.is_private = True
            if current_user:
                existing.owner_id = current_user.id
            await db.commit()
            from app.worker.tasks import clone_and_index
            clone_and_index.delay(str(existing.id), github_token)
            await db.refresh(existing)
            return RepoResponse.model_validate(existing)

    # Create new repository record
    repo = Repository(
        github_url=canonical_url,
        owner=owner,
        repo_name=repo_name,
        status=RepositoryStatus.pending,
        is_private=bool(github_token),
        owner_id=current_user.id if current_user else None,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)

    # Enqueue indexing task (pass token for private repos)
    from app.worker.tasks import clone_and_index
    clone_and_index.delay(str(repo.id), github_token)

    logger.info(
        "Repository submitted for indexing",
        extra={
            "repo_id": str(repo.id),
            "github_url": canonical_url,
            "is_private": repo.is_private,
        },
    )

    return RepoResponse.model_validate(repo)


@router.get(
    "/{repo_id}",
    response_model=RepoResponse,
    summary="Get repository indexing status",
)
async def get_repo(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return RepoResponse.model_validate(repo)


@router.get(
    "/{repo_id}/files",
    response_model=FileListResponse,
    summary="List indexed files for a repository",
)
async def list_files(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=50, ge=1, le=200, description="Items per page"),
):
    # Verify repo exists
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    if repo.status != RepositoryStatus.ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Repository is not ready for querying (status: {repo.status.value})",
        )

    # Count total
    count_result = await db.execute(
        select(func.count(File.id)).where(File.repository_id == repo_id)
    )
    total = count_result.scalar_one()

    # Paginated query
    offset = (page - 1) * page_size
    files_result = await db.execute(
        select(File)
        .where(File.repository_id == repo_id)
        .order_by(File.path)
        .offset(offset)
        .limit(page_size)
    )
    files = files_result.scalars().all()

    return FileListResponse(
        items=[FileResponse.model_validate(f) for f in files],
        total=total,
        page=page,
        page_size=page_size,
    )
