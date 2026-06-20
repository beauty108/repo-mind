"""
BM25 lexical search service for hybrid retrieval.

Builds an in-memory BM25 index over all code chunks for a repository.
Used alongside vector search and merged via Reciprocal Rank Fusion (RRF)
to improve recall for exact symbol names, error codes, and specific identifiers.

The BM25 corpus is fetched from the DB once per repo and cached in Redis
(serialized as JSON) keyed by repo_id + indexed_commit_sha. Cache is invalidated
automatically when the repo is re-indexed (new commit SHA).
"""

from __future__ import annotations

import json
import logging
import uuid
from functools import lru_cache

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    """
    Simple tokenizer: lowercase, split on whitespace and common code punctuation.
    Keeps identifiers intact (e.g., 'my_function' stays as one token).
    """
    import re
    # Split on whitespace and punctuation that isn't part of identifiers
    tokens = re.split(r"[\s\(\)\[\]{}<>\"\',:;=+\-*/\\@#!&|^~`]+", text.lower())
    return [t for t in tokens if len(t) > 1]


def _get_redis():
    """Get Redis client (returns None if Redis is unavailable)."""
    try:
        import redis
        from app.config import get_settings
        settings = get_settings()
        client = redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception as e:
        logger.debug("Redis not available for BM25 cache", extra={"error": str(e)})
        return None


def _get_corpus_from_db(repo_id: str) -> list[dict] | None:
    """
    Fetch all chunk texts and IDs for a repo from the DB (sync SQLAlchemy).
    Returns list of {id, content} dicts or None on failure.
    """
    try:
        from app.config import get_settings
        from sqlalchemy import create_engine, text

        settings = get_settings()
        engine = create_engine(settings.sync_database_url, pool_pre_ping=True)

        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT cc.id::text, cc.content
                    FROM code_chunks cc
                    JOIN files f ON f.id = cc.file_id
                    WHERE f.repository_id = :repo_id
                    ORDER BY cc.id
                """),
                {"repo_id": repo_id},
            )
            rows = result.fetchall()

        logger.info(
            "BM25 corpus loaded from DB",
            extra={"repo_id": repo_id, "chunk_count": len(rows)},
        )
        return [{"id": row[0], "content": row[1]} for row in rows]
    except Exception as e:
        logger.error("Failed to load BM25 corpus from DB", extra={"error": str(e)})
        return None


def _get_repo_commit_sha(repo_id: str) -> str | None:
    """Get the current indexed_commit_sha for a repo (used as cache key)."""
    try:
        from app.config import get_settings
        from sqlalchemy import create_engine, text

        settings = get_settings()
        engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT indexed_commit_sha FROM repositories WHERE id = :repo_id"),
                {"repo_id": repo_id},
            )
            row = result.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _cache_key(repo_id: str, commit_sha: str | None) -> str:
    return f"repomind:bm25:corpus:{repo_id}:{commit_sha or 'unknown'}"


def _load_corpus(repo_id: str) -> list[dict] | None:
    """
    Load chunk corpus from Redis cache if available, otherwise fetch from DB and cache.
    Returns list of {id, content} dicts.
    """
    commit_sha = _get_repo_commit_sha(repo_id)
    key = _cache_key(repo_id, commit_sha)

    redis_client = _get_redis()
    if redis_client:
        try:
            cached = redis_client.get(key)
            if cached:
                corpus = json.loads(cached)
                logger.debug(
                    "BM25 corpus loaded from Redis cache",
                    extra={"repo_id": repo_id, "chunk_count": len(corpus)},
                )
                return corpus
        except Exception as e:
            logger.debug("BM25 cache read failed", extra={"error": str(e)})

    # Fetch from DB
    corpus = _get_corpus_from_db(repo_id)
    if corpus is None:
        return None

    # Cache in Redis for 1 hour
    if redis_client and corpus:
        try:
            redis_client.setex(key, 3600, json.dumps(corpus))
        except Exception as e:
            logger.debug("BM25 cache write failed", extra={"error": str(e)})

    return corpus


def bm25_search(repo_id: str, query: str, top_k: int) -> list[uuid.UUID]:
    """
    Run BM25 lexical search over all chunks for a repository.

    Args:
        repo_id: Repository UUID as string.
        query: Raw query text (not embedded).
        top_k: Number of top chunk IDs to return.

    Returns:
        List of chunk UUIDs ranked by BM25 score (best first).
        Returns empty list on any failure — callers should fall back to vector-only.
    """
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning(
            "rank-bm25 not installed — BM25 search unavailable. "
            "Install with: pip install rank-bm25"
        )
        return []

    corpus = _load_corpus(repo_id)
    if not corpus:
        logger.debug("BM25 corpus empty for repo", extra={"repo_id": repo_id})
        return []

    # Build tokenized corpus
    tokenized_corpus = [_tokenize(doc["content"]) for doc in corpus]
    tokenized_query = _tokenize(query)

    if not tokenized_query:
        return []

    try:
        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(tokenized_query)

        # Rank by score descending, take top_k
        indexed_scores = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        top_indices = [i for i, score in indexed_scores[:top_k] if score > 0]

        result_ids = [uuid.UUID(corpus[i]["id"]) for i in top_indices]

        logger.debug(
            "BM25 search complete",
            extra={
                "repo_id": repo_id,
                "query_tokens": len(tokenized_query),
                "results": len(result_ids),
            },
        )

        return result_ids

    except Exception as e:
        logger.error("BM25 search failed", extra={"repo_id": repo_id, "error": str(e)})
        return []


def invalidate_bm25_cache(repo_id: str) -> None:
    """
    Invalidate the BM25 corpus cache for a repository.
    Called after re-indexing to force fresh corpus load.
    """
    redis_client = _get_redis()
    if not redis_client:
        return
    try:
        # Delete all cache keys for this repo (any commit SHA)
        pattern = f"repomind:bm25:corpus:{repo_id}:*"
        keys = redis_client.keys(pattern)
        if keys:
            redis_client.delete(*keys)
            logger.info(
                "BM25 cache invalidated",
                extra={"repo_id": repo_id, "keys_deleted": len(keys)},
            )
    except Exception as e:
        logger.debug("BM25 cache invalidation failed", extra={"error": str(e)})
