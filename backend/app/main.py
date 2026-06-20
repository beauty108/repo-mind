"""
RepoMind FastAPI application factory.

Sets up:
  - CORS middleware
  - Rate limiting (slowapi) on indexing and chat endpoints
  - Structured JSON logging
  - Router registration
  - Health check endpoint
  - Startup/shutdown lifecycle hooks
"""

from __future__ import annotations

import logging
import sys

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.config import get_settings

# ── Logging setup ──────────────────────────────────────────────────────────────

def _configure_logging() -> None:
    """Configure structured JSON logging for the entire application."""
    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    # Suppress noisy third-party loggers
    for noisy in ("urllib3", "httpx", "httpcore", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger(__name__)


# ── Shared rate limiter (used by route decorators in routers) ─────────────────
# Import here so routers can import it from app.main without circular imports.
limiter = Limiter(key_func=get_remote_address)


# ── App factory ────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="RepoMind API",
        description=(
            "A Retrieval-Augmented Generation (RAG) system for querying public "
            "GitHub repositories via natural language. Submit a repo URL, wait for "
            "indexing, then ask questions about the codebase.\n\n"
            "**Non-goals**: RepoMind does NOT execute repository code, generate "
            "code modifications, run shell commands from repo contents, or open PRs."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=[
            {"name": "repositories", "description": "Submit and monitor repository indexing"},
            {"name": "chat", "description": "Chat with a repository (SSE streaming)"},
            {"name": "health", "description": "Health checks"},
        ],
    )

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # CORS — allow the frontend dev server origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    )

    # ── Routers ────────────────────────────────────────────────────────────────
    from app.api.repos import router as repos_router
    from app.api.chat import router as chat_router
    from app.api.auth import router as auth_router

    app.include_router(repos_router)
    app.include_router(chat_router)
    app.include_router(auth_router)

    # ── Health check ───────────────────────────────────────────────────────────
    @app.get("/health", tags=["health"], include_in_schema=True)
    async def health_check():
        """Returns 200 OK if the API is running."""
        return {"status": "ok", "service": "repomind-api", "version": "1.0.0"}

    # ── Global error handler ───────────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        # RateLimitExceeded is handled by slowapi — don't interfere
        if isinstance(exc, RateLimitExceeded):
            raise exc
        logger.exception(
            "Unhandled exception",
            extra={"path": str(request.url), "error": str(exc)},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal server error occurred. Please try again."},
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def on_startup():
        logger.info("RepoMind API starting", extra={"cors_origins": settings.cors_origins})
        try:
            from app.database import get_engine
            from sqlalchemy import text
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Database connection verified")
        except Exception as e:
            logger.error(
                "Database connection failed on startup — check DATABASE_URL",
                extra={"error": str(e)},
            )

    @app.on_event("shutdown")
    async def on_shutdown():
        logger.info("RepoMind API shutting down")
        try:
            from app.database import get_engine
            await get_engine().dispose()
        except Exception:
            pass

    return app


app = create_app()
