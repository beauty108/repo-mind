"""
FastAPI dependency injectors.

get_db             — yields an async SQLAlchemy session
get_current_user   — requires valid JWT Bearer token, returns User
get_optional_user  — returns User if JWT present, else None (for backward-compat endpoints)
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

# ── Auth dependencies ─────────────────────────────────────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    """
    Dependency: validate JWT Bearer token and return the authenticated User.
    Raises 401 if no token or invalid token.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Include an Authorization: Bearer <token> header.",
        )

    from app.services.auth import decode_access_token
    from app.models.user import User
    from sqlalchemy import select

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please log in again.",
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.")

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found.",
        )

    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    """
    Dependency: return the authenticated User if a valid JWT is present, else None.
    Used for backward-compatible endpoints that work both authed and unauthed.
    """
    if credentials is None:
        return None

    try:
        return await get_current_user(credentials=credentials, db=db)
    except HTTPException:
        return None
