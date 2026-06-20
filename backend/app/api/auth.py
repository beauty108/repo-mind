"""
Auth API endpoints.

POST /api/auth/register  — create a new user account
POST /api/auth/login     — return JWT access token
GET  /api/auth/me        — return current user profile
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Request / Response schemas ─────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, description="Password (min 8 characters)")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    has_github_token: bool

    model_config = {"from_attributes": True}


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User
    from app.services.auth import hash_password, create_access_token

    # Check if email already taken
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    # Create user
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Issue token
    token = create_access_token({"sub": str(user.id)})

    logger.info("New user registered", extra={"user_id": str(user.id)})

    return TokenResponse(
        access_token=token,
        user_id=str(user.id),
        email=user.email,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and get a JWT access token",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User
    from app.services.auth import verify_password, create_access_token

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # Use same error for missing user vs wrong password to prevent user enumeration
    invalid_creds = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
    )

    if user is None:
        raise invalid_creds

    if not verify_password(body.password, user.hashed_password):
        raise invalid_creds

    token = create_access_token({"sub": str(user.id)})

    logger.info("User logged in", extra={"user_id": str(user.id)})

    return TokenResponse(
        access_token=token,
        user_id=str(user.id),
        email=user.email,
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def get_me(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Returns the current authenticated user's profile."""
    # This endpoint requires auth — wired up in main.py after deps are ready
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        has_github_token=current_user.github_access_token is not None,
    )


# ── GitHub OAuth ───────────────────────────────────────────────────────────────

from fastapi.responses import RedirectResponse
import httpx
from app.config import get_settings

@router.get(
    "/github/login",
    summary="Redirect to GitHub OAuth login",
    description="Initiates the GitHub OAuth flow to request repository access."
)
async def github_login():
    settings = get_settings()
    if not settings.github_client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub OAuth is not configured on the server."
        )
    
    # Request 'repo' scope for full private repo read/write (needed to clone private repos),
    # and 'user:email' to map the account.
    url = f"https://github.com/login/oauth/authorize?client_id={settings.github_client_id}&scope=repo,user:email"
    return RedirectResponse(url)


@router.get(
    "/github/callback",
    summary="Handle GitHub OAuth callback",
    description="Exchanges the authorization code for a token and creates/updates the user."
)
async def github_callback(
    code: str,
    db: AsyncSession = Depends(get_db)
):
    settings = get_settings()
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub OAuth is not configured on the server."
        )

    async with httpx.AsyncClient() as client:
        # 1. Exchange code for access token
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            }
        )
        token_data = token_res.json()
        access_token = token_data.get("access_token")
        if not access_token:
            logger.error("Failed to retrieve GitHub access token", extra={"response": token_data})
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve GitHub access token. The code may have expired."
            )
        
        # 2. Fetch user's emails to identify them
        emails_res = await client.get(
            "https://api.github.com/user/emails",
            headers={
                "Authorization": f"token {access_token}",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        emails_res.raise_for_status()
        emails = emails_res.json()
        primary_email = next((e["email"] for e in emails if e.get("primary")), None)
        if not primary_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No primary email found on the linked GitHub account."
            )
        
    # 3. Create or update the user in our database
    from app.models.user import User
    from app.services.auth import create_access_token
    
    result = await db.execute(select(User).where(User.email == primary_email))
    user = result.scalar_one_or_none()
    
    if not user:
        # Create a new user with a dummy password since they authenticate via OAuth
        user = User(
            email=primary_email,
            hashed_password="oauth_user_no_password",
            github_access_token=access_token
        )
        db.add(user)
        logger.info("New user registered via GitHub OAuth", extra={"email": primary_email})
    else:
        # Update existing user's GitHub token
        user.github_access_token = access_token
        logger.info("Existing user linked GitHub account", extra={"user_id": str(user.id)})
    
    await db.commit()
    await db.refresh(user)
    
    # 4. Issue RepoMind internal JWT
    jwt_token = create_access_token({"sub": str(user.id)})
    
    # 5. Redirect back to frontend
    # Use the first CORS origin as the frontend URL, or fallback to localhost
    frontend_url = settings.cors_origins[0] if settings.cors_origins else "http://localhost:5173"
    
    # Redirect to a frontend route that saves the token and logs the user in
    return RedirectResponse(f"{frontend_url}/oauth/callback?token={jwt_token}")

