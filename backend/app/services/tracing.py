"""
Langfuse observability tracing for the RAG pipeline.

Logs every retrieval + LLM call to Langfuse for empirical debugging and tuning.
This makes it possible to answer questions like:
  - Why did the model give a bad answer? (what chunks were retrieved?)
  - Which queries have the highest/lowest similarity scores?
  - What is the P95 retrieval latency?

The tracer is best-effort: if Langfuse is not configured or unavailable,
all calls silently no-op. The RAG pipeline is never blocked by tracing failures.

Configuration (all optional):
    LANGFUSE_PUBLIC_KEY=pk-...
    LANGFUSE_SECRET_KEY=sk-...
    LANGFUSE_HOST=https://cloud.langfuse.com  (or self-hosted URL)
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.rag import RetrievedChunk

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_langfuse():
    """
    Return a Langfuse client if configured, or None.
    Cached after first call — only initializes once.
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.info(
            "Langfuse tracing disabled — set LANGFUSE_PUBLIC_KEY and "
            "LANGFUSE_SECRET_KEY to enable observability"
        )
        return None

    try:
        from langfuse import Langfuse
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        logger.info(
            "Langfuse tracing initialized",
            extra={"host": settings.langfuse_host},
        )
        return client
    except ImportError:
        logger.warning(
            "langfuse package not installed — tracing disabled. "
            "Install with: pip install langfuse"
        )
        return None
    except Exception as e:
        logger.warning(
            "Failed to initialize Langfuse — tracing disabled",
            extra={"error": str(e)},
        )
        return None


def trace_rag_call(
    *,
    repo_id: str,
    query: str,
    retrieved_chunks: list["RetrievedChunk"],
    reranked_chunks: list["RetrievedChunk"],
    answer: str,
    latency_s: float,
    conversation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Emit a structured RAG trace to Langfuse.

    Args:
        repo_id: Repository UUID string.
        query: The user's question.
        retrieved_chunks: Raw chunks from vector+BM25 retrieval (before reranking).
        reranked_chunks: Final chunks passed to the LLM (after reranking).
        answer: The full LLM-generated answer.
        latency_s: End-to-end pipeline latency in seconds.
        conversation_id: Optional conversation UUID for grouping traces.
        metadata: Additional key-value pairs to attach to the trace.
    """
    lf = _get_langfuse()
    if lf is None:
        return

    try:
        trace = lf.trace(
            name="rag_pipeline",
            input={"query": query},
            output={"answer": answer[:500] + "..." if len(answer) > 500 else answer},
            metadata={
                "repo_id": repo_id,
                "conversation_id": conversation_id,
                "retrieval_count": len(retrieved_chunks),
                "reranked_count": len(reranked_chunks),
                "latency_s": round(latency_s, 3),
                **(metadata or {}),
            },
        )

        # Log retrieval span
        trace.span(
            name="retrieval",
            input={"query": query, "repo_id": repo_id},
            output={
                "chunks": [
                    {
                        "file": c.file_path,
                        "lines": f"{c.start_line}-{c.end_line}",
                        "similarity": c.similarity,
                        "symbol": c.symbol_name,
                    }
                    for c in retrieved_chunks[:10]  # Log top-10 only
                ]
            },
        )

        # Log reranking span
        if len(reranked_chunks) != len(retrieved_chunks):
            trace.span(
                name="reranking",
                input={"candidates": len(retrieved_chunks)},
                output={"selected": len(reranked_chunks)},
            )

        # Log generation span
        trace.span(
            name="generation",
            input={
                "context_chunks": len(reranked_chunks),
                "query": query,
            },
            output={"answer_length": len(answer)},
            metadata={"latency_s": round(latency_s, 3)},
        )

    except Exception as e:
        # Never let tracing failures impact the user
        logger.debug("Langfuse trace emission failed", extra={"error": str(e)})
