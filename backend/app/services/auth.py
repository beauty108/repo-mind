"""
Auth service — password hashing, JWT creation, and token decoding.

Uses:
  - bcrypt for secure password hashing (via passlib)
  - python-jose for JWT signing/verification
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(plain_password: str) -> str:
    """Hash a plain-text password using bcrypt."""
    try:
        import bcrypt
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
        return hashed.decode("utf-8")
    except ImportError:
        raise RuntimeError("bcrypt is required. Install with: pip install bcrypt")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against a bcrypt hash."""
    try:
        import bcrypt
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except (ImportError, Exception) as e:
        logger.error("Password verification failed", extra={"error": str(e)})
        return False


# ── JWT tokens ────────────────────────────────────────────────────────────────

def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """
    Create a signed JWT access token.

    Args:
        data: Payload to encode (typically {"sub": user_id_str}).
        expires_delta: Token lifetime. Defaults to settings.access_token_expire_minutes.

    Returns:
        Encoded JWT string.
    """
    from app.config import get_settings
    settings = get_settings()

    try:
        from jose import jwt
    except ImportError:
        raise RuntimeError("python-jose is required. Install with: pip install python-jose[cryptography]")

    to_encode = data.copy()
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_expire_minutes)

    expire = datetime.now(timezone.utc) + expires_delta
    to_encode["exp"] = expire

    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """
    Decode and verify a JWT access token.

    Returns:
        Decoded payload dict, or None if the token is invalid/expired.
    """
    from app.config import get_settings
    settings = get_settings()

    try:
        from jose import jwt, JWTError
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except Exception:
        return None
