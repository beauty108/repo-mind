"""
Application configuration — all settings loaded from environment variables.
Never import this module at the top level of a module that is imported by Celery
tasks; use get_settings() to lazily fetch the singleton.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://repomind:repomind@localhost:5432/repomind",
        description="Async SQLAlchemy DSN (must use asyncpg driver).",
    )

    # Sync DSN used only by Alembic (asyncpg doesn't work with Alembic's sync runner)
    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )

    # ── Redis ───────────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL (used as Celery broker and result backend).",
    )

    # ── Embedding ───────────────────────────────────────────────────────────
    embedding_provider: Literal["local", "openai"] = Field(
        default="local",
        description="Which embedding provider to use. Set to 'openai' to use OpenAI.",
    )
    local_embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="HuggingFace model ID for the local sentence-transformers provider.",
    )
    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI API key. Required only when EMBEDDING_PROVIDER=openai.",
    )

    # ── LLM ─────────────────────────────────────────────────────────────────
    llm_provider: Literal["gemini"] = Field(
        default="gemini",
        description="LLM provider. Currently only 'gemini' is implemented.",
    )
    gemini_api_key: str | None = Field(
        default=None,
        description="Google Gemini API key. Required when LLM_PROVIDER=gemini.",
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description=(
            "Gemini model name. Verify current free-tier availability at "
            "https://ai.google.dev/gemini-api/docs/models"
        ),
    )

    # ── Repository Ingestion ─────────────────────────────────────────────────
    max_repo_size_mb: int = Field(
        default=500,
        description="Hard cap on repository clone size in megabytes.",
    )
    clone_timeout_seconds: int = Field(
        default=120,
        description="Maximum seconds allowed for a git clone operation.",
    )
    clone_base_dir: str = Field(
        default="/tmp/repomind",
        description="Base directory for temporary repository clones per job.",
    )

    # ── Retrieval / RAG ──────────────────────────────────────────────────────
    top_k: int = Field(
        default=8,
        description="Final number of chunks passed to the LLM after reranking.",
    )
    max_context_chunks: int = Field(
        default=8,
        description=(
            "Maximum chunks included in the final LLM prompt. "
            "Separate from TOP_K to support reranking that may trim candidates."
        ),
    )

    # ── Hybrid Search (BM25 + Vector + RRF) ─────────────────────────────────
    hybrid_search_enabled: bool = Field(
        default=True,
        description="Enable hybrid search: BM25 + vector search merged via RRF.",
    )
    top_k_vector: int = Field(
        default=20,
        description=(
            "Number of vector search candidates retrieved before RRF merge. "
            "Should be >= top_k. Larger values improve recall at some latency cost."
        ),
    )
    top_k_bm25: int = Field(
        default=20,
        description="Number of BM25 candidates retrieved before RRF merge.",
    )

    # ── Reranking ────────────────────────────────────────────────────────────
    reranker_enabled: bool = Field(
        default=True,
        description=(
            "Enable cross-encoder reranking after retrieval. "
            "Requires BAAI/bge-reranker-base to be downloaded (~500MB) on first use."
        ),
    )
    reranker_model: str = Field(
        default="BAAI/bge-reranker-base",
        description="HuggingFace cross-encoder model for reranking.",
    )
    reranker_top_k: int = Field(
        default=5,
        description="Number of chunks to keep after reranking (becomes the LLM context).",
    )

    # ── Query Decomposition ───────────────────────────────────────────────────
    query_decomposition_enabled: bool = Field(
        default=True,
        description=(
            "Enable query decomposition for complex multi-part questions. "
            "Sub-queries are answered separately then synthesized."
        ),
    )
    max_sub_queries: int = Field(
        default=3,
        description="Maximum number of sub-queries to decompose a complex question into.",
    )

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="Allowed CORS origins (comma-separated string or JSON list).",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    # ── Rate Limiting ────────────────────────────────────────────────────────
    rate_limit_index: str = Field(
        default="5/minute",
        description="slowapi rate limit string for POST /api/repos.",
    )
    rate_limit_chat: str = Field(
        default="10/minute",
        description="slowapi rate limit string for POST /api/repos/{id}/chat.",
    )

    # ── Cache ────────────────────────────────────────────────────────────────
    chat_cache_ttl: int = Field(
        default=300,
        description="Redis TTL in seconds for chat response cache. 0 to disable.",
    )

    # ── Observability (Langfuse) ──────────────────────────────────────────────
    langfuse_public_key: str | None = Field(
        default=None,
        description="Langfuse public key for RAG pipeline tracing.",
    )
    langfuse_secret_key: str | None = Field(
        default=None,
        description="Langfuse secret key for RAG pipeline tracing.",
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        description="Langfuse server URL (use self-hosted URL if running locally).",
    )

    # ── Auth & OAuth ────────────────────────────────────────────────────────
    secret_key: str = Field(
        default="change-me-in-production-use-a-long-random-string",
        description="Secret key for signing JWT access tokens. MUST be changed in production.",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm.",
    )
    access_token_expire_minutes: int = Field(
        default=60 * 24 * 7,  # 7 days
        description="JWT access token expiry in minutes (default: 7 days).",
    )
    github_client_id: str | None = Field(
        default=None,
        description="GitHub OAuth App Client ID (for indexing private repos).",
    )
    github_client_secret: str | None = Field(
        default=None,
        description="GitHub OAuth App Client Secret.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()
