"""
Redis cache helpers for chat response caching and stream cancellation.

Caching strategy:
  - Cache key: hash of (repo_id, commit_sha, question)
  - Value: complete assistant response text + citations as JSON
  - TTL: configurable (CHAT_CACHE_TTL env var, default 300s)
  - Never serves cached answers for different commit SHAs

Stream cancellation:
  - Each SSE stream gets a unique stream_id
  - DELETE /api/repos/{id}/chat/stream/{stream_id} sets a cancellation flag in Redis
  - The SSE generator checks this flag between yields
  - Cancellation keys expire after 5 minutes automatically

The cache is best-effort: failures are logged and silently bypassed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_redis_client():
    from app.config import get_settings
    try:
        import redis
        settings = get_settings()
        client = redis.from_url(settings.redis_url, decode_responses=True)
        return client
    except Exception as e:
        logger.warning("Could not connect to Redis for caching", extra={"error": str(e)})
        return None


def _make_cache_key(repo_id: str, commit_sha: str | None, question: str) -> str:
    """Build a deterministic cache key that binds to the commit SHA."""
    raw = f"chat:{repo_id}:{commit_sha or 'unknown'}:{question.strip().lower()}"
    return "repomind:chat:" + hashlib.sha256(raw.encode()).hexdigest()


def get_cached_chat_semantic(
    repo_id: str, commit_sha: str | None, query_vector: list[float], threshold: float = 0.95
) -> tuple[str, list[dict]] | None:
    """
    Return (answer_text, citations) from cache if a semantically similar query exists.
    """
    from app.config import get_settings
    settings = get_settings()
    if not settings.chat_cache_ttl:
        return None

    client = _get_redis_client()
    if client is None:
        return None

    try:
        import numpy as np
        
        # We store all cached queries for this repo/commit in a Redis list
        key = f"repomind:chat_list:{repo_id}:{commit_sha or 'unknown'}"
        items = client.lrange(key, 0, -1)
        
        if not items:
            return None
            
        best_match = None
        best_score = -1.0
        
        q_vec = np.array(query_vector)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return None
            
        for item_str in items:
            item = json.loads(item_str)
            cached_vec = np.array(item["vector"])
            
            # Cosine similarity
            c_norm = np.linalg.norm(cached_vec)
            if c_norm == 0:
                continue
                
            sim = np.dot(q_vec, cached_vec) / (q_norm * c_norm)
            
            if sim > best_score:
                best_score = sim
                best_match = item
                
        if best_match and best_score >= threshold:
            logger.info("Semantic cache hit", extra={"score": float(best_score), "repo_id": repo_id})
            return best_match["answer"], best_match["citations"]
            
    except Exception as e:
        logger.debug("Semantic cache get failed", extra={"error": str(e)})
    return None


def set_cached_chat_semantic(
    repo_id: str,
    commit_sha: str | None,
    question: str,
    query_vector: list[float],
    answer: str,
    citations: list[dict],
) -> None:
    """
    Store a chat response and its vector in Redis for semantic matching.
    """
    from app.config import get_settings
    settings = get_settings()
    if not settings.chat_cache_ttl:
        return

    client = _get_redis_client()
    if client is None:
        return

    try:
        key = f"repomind:chat_list:{repo_id}:{commit_sha or 'unknown'}"
        
        value = json.dumps({
            "question": question,
            "vector": query_vector,
            "answer": answer,
            "citations": citations
        })
        
        # Append to the list
        client.rpush(key, value)
        # Set expiry on the list if it doesn't have one
        if client.ttl(key) == -1:
            client.expire(key, settings.chat_cache_ttl)
            
    except Exception as e:
        logger.debug("Semantic cache set failed", extra={"error": str(e)})


# ── Stream Cancellation ───────────────────────────────────────────────────────

def set_stream_cancelled(stream_id: str) -> None:
    """
    Mark a stream as cancelled. The SSE generator will check this flag
    between token yields and stop emitting if it is set.
    Cancellation keys expire after 5 minutes automatically.
    """
    client = _get_redis_client()
    if client is None:
        return
    try:
        key = f"repomind:stream:cancelled:{stream_id}"
        client.setex(key, 300, "1")  # 5-minute TTL
        logger.info("Stream cancellation signalled", extra={"stream_id": stream_id})
    except Exception as e:
        logger.debug("Stream cancellation set failed", extra={"error": str(e)})


def is_stream_cancelled(stream_id: str) -> bool:
    """
    Check whether a stream has been cancelled.
    Returns False if Redis is unavailable (fail-open — never block the stream).
    """
    client = _get_redis_client()
    if client is None:
        return False
    try:
        key = f"repomind:stream:cancelled:{stream_id}"
        return client.exists(key) > 0
    except Exception:
        return False
