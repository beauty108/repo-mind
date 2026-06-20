"""
RAG pipeline: retrieval, context assembly, and streaming generation.

Responsibilities:
  1. embed_query — embed the user's question via EmbeddingClient
  2. retrieve_chunks — cosine similarity search in pgvector (+ optional BM25 hybrid)
  3. validate_model_consistency — ensure query uses same model as index
  4. build_prompt — assemble retrieved chunks + conversation history into an LLM prompt
  5. stream_rag_answer — yield tokens from LLMClient (with conversation context)
  6. build_citations — return structured citation list from chunk metadata
  7. delete_chunks_for_files — incremental re-index helper
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# System prompt injected into every chat request
_RAG_SYSTEM_PROMPT = """You are RepoMind, an expert code analysis assistant.
You answer questions about a specific software repository using ONLY the code context provided below.
Rules:
1. Ground every answer in the provided code snippets.
2. Reference specific files and functions when relevant.
3. If the answer cannot be determined from the context, say so clearly — do NOT hallucinate.
4. Do NOT generate code modifications, patches, or pull requests.
5. Be precise, technical, and concise.
6. When there is prior conversation history, refer back to it naturally — say "as I mentioned" or "building on my previous answer" where appropriate."""

# Maximum conversation turns to include in context (3 pairs = 6 messages)
_MAX_HISTORY_TURNS = 3


@dataclass
class RetrievedChunk:
    """A code chunk retrieved from the vector store."""
    chunk_id: uuid.UUID
    file_path: str
    language: str
    content: str
    symbol_name: str | None
    symbol_type: str
    start_line: int
    end_line: int
    similarity: float


def embed_query(question: str) -> list[float]:
    """Embed the user's question using the active EmbeddingClient."""
    from app.ai.embedding_client import get_embedding_client, EmbeddingError
    client = get_embedding_client()
    try:
        vectors = client.embed([question])
        return vectors[0]
    except EmbeddingError as e:
        raise ValueError(f"Failed to embed query: {e}") from e


async def validate_model_consistency(db: AsyncSession, repo_id: uuid.UUID) -> None:
    """
    Validate that the active embedding model matches the one used at index time.
    Raises ValueError with a clear message if they don't match.
    """
    from sqlalchemy import select
    from app.models.repository import Repository
    from app.ai.embedding_client import get_embedding_client

    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        return  # Will be caught by endpoint

    active_model = get_embedding_client().model_name
    if repo.embedding_model_name and repo.embedding_model_name != active_model:
        raise ValueError(
            f"Model mismatch: this repository was indexed with '{repo.embedding_model_name}' "
            f"but the active embedding model is '{active_model}'. "
            "Please re-index the repository or switch back to the original embedding provider. "
            "Querying with a different model would produce meaningless results."
        )


async def retrieve_chunks(
    db: AsyncSession,
    repo_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
) -> list[RetrievedChunk]:
    """
    Retrieve top-k code chunks most similar to query_vector via pgvector cosine search.
    Falls back to vector-only when hybrid search is not available.
    """
    return await _retrieve_vector_only(db, repo_id, query_vector, top_k)


async def _retrieve_vector_only(
    db: AsyncSession,
    repo_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
) -> list[RetrievedChunk]:
    """Pure vector similarity retrieval via pgvector."""
    from sqlalchemy import text

    retrieval_start = time.time()

    # Use raw SQL for pgvector cosine distance operator (<=>)
    # 1 - cosine_distance = cosine_similarity
    sql = text("""
        SELECT
            cc.id AS chunk_id,
            f.path AS file_path,
            f.language,
            cc.content,
            cc.symbol_name,
            cc.symbol_type,
            cc.start_line,
            cc.end_line,
            1 - (e.vector <=> CAST(:query_vector AS vector)) AS similarity
        FROM embeddings e
        JOIN code_chunks cc ON cc.id = e.chunk_id
        JOIN files f ON f.id = cc.file_id
        WHERE f.repository_id = :repo_id
        ORDER BY e.vector <=> CAST(:query_vector AS vector)
        LIMIT :top_k
    """)

    result = await db.execute(
        sql,
        {
            "query_vector": str(query_vector),
            "repo_id": str(repo_id),
            "top_k": top_k,
        },
    )
    rows = result.fetchall()

    retrieval_duration = time.time() - retrieval_start
    logger.info(
        "Vector retrieval complete",
        extra={
            "repo_id": str(repo_id),
            "top_k": top_k,
            "retrieved": len(rows),
            "retrieval_latency_s": round(retrieval_duration, 4),
        },
    )

    return [
        RetrievedChunk(
            chunk_id=row.chunk_id,
            file_path=row.file_path,
            language=row.language,
            content=row.content,
            symbol_name=row.symbol_name,
            symbol_type=row.symbol_type,
            start_line=row.start_line,
            end_line=row.end_line,
            similarity=float(row.similarity),
        )
        for row in rows
    ]


async def retrieve_chunks_hybrid(
    db: AsyncSession,
    repo_id: uuid.UUID,
    query_vector: list[float],
    query_text: str,
    top_k: int,
) -> list[RetrievedChunk]:
    """
    Full hybrid retrieval: vector search + BM25, merged via Reciprocal Rank Fusion.
    Falls back to vector-only if BM25 fails.
    """
    import asyncio
    from app.config import get_settings
    from app.services.bm25_search import bm25_search
    from app.services.rrf import reciprocal_rank_fusion

    settings = get_settings()
    candidate_k = settings.top_k_vector  # larger pool before RRF

    retrieval_start = time.time()

    # Run vector and BM25 concurrently
    vector_task = asyncio.ensure_future(
        _retrieve_vector_only(db, repo_id, query_vector, candidate_k)
    )
    loop = asyncio.get_event_loop()
    bm25_task = loop.run_in_executor(
        None, bm25_search, str(repo_id), query_text, candidate_k
    )

    vector_chunks, bm25_chunk_ids = await asyncio.gather(
        vector_task, bm25_task, return_exceptions=True
    )

    retrieval_duration = time.time() - retrieval_start

    # Handle failures gracefully
    if isinstance(vector_chunks, Exception):
        logger.error("Vector search failed in hybrid mode", extra={"error": str(vector_chunks)})
        raise vector_chunks

    if isinstance(bm25_chunk_ids, Exception) or not bm25_chunk_ids:
        logger.warning("BM25 search failed or empty, falling back to vector-only")
        return vector_chunks[:top_k]

    # Merge via RRF
    vector_ids = [c.chunk_id for c in vector_chunks]
    merged_ids = reciprocal_rank_fusion(vector_ids, bm25_chunk_ids)

    # Map chunks by ID; fetch any BM25-only chunks not in vector results
    chunk_map = {c.chunk_id: c for c in vector_chunks}
    bm25_only_ids = [cid for cid in merged_ids if cid not in chunk_map]

    if bm25_only_ids:
        bm25_only_chunks = await _fetch_chunks_by_ids(db, bm25_only_ids)
        for c in bm25_only_chunks:
            chunk_map[c.chunk_id] = c

    result = [chunk_map[cid] for cid in merged_ids[:top_k] if cid in chunk_map]

    logger.info(
        "Hybrid retrieval complete",
        extra={
            "repo_id": str(repo_id),
            "vector_candidates": len(vector_chunks),
            "bm25_candidates": len(bm25_chunk_ids),
            "merged_top_k": len(result),
            "retrieval_latency_s": round(retrieval_duration, 4),
        },
    )

    return result if result else vector_chunks[:top_k]


async def _fetch_chunks_by_ids(
    db: AsyncSession,
    chunk_ids: list[uuid.UUID],
) -> list[RetrievedChunk]:
    """Fetch specific chunks by ID (for BM25-only results not in vector results)."""
    from sqlalchemy import text

    if not chunk_ids:
        return []

    id_strs = [str(cid) for cid in chunk_ids]
    sql = text("""
        SELECT
            cc.id AS chunk_id,
            f.path AS file_path,
            f.language,
            cc.content,
            cc.symbol_name,
            cc.symbol_type,
            cc.start_line,
            cc.end_line,
            0.5 AS similarity
        FROM code_chunks cc
        JOIN files f ON f.id = cc.file_id
        WHERE cc.id = ANY(CAST(:ids AS uuid[]))
    """)

    result = await db.execute(sql, {"ids": "{" + ",".join(id_strs) + "}"})
    rows = result.fetchall()

    return [
        RetrievedChunk(
            chunk_id=row.chunk_id,
            file_path=row.file_path,
            language=row.language,
            content=row.content,
            symbol_name=row.symbol_name,
            symbol_type=row.symbol_type,
            start_line=row.start_line,
            end_line=row.end_line,
            similarity=float(row.similarity),
        )
        for row in rows
    ]


def build_prompt(
    question: str,
    chunks: list[RetrievedChunk],
    conversation_history: list[dict] | None = None,
) -> str:
    """
    Assemble the LLM prompt from the user's question, retrieved chunks,
    and optional conversation history.

    Each chunk is formatted with its file path, line range, and content.
    Citations are derived from chunk metadata — NOT from LLM output.
    Conversation history is capped at the last _MAX_HISTORY_TURNS pairs.
    """
    context_parts: list[str] = []

    for i, chunk in enumerate(chunks, 1):
        symbol_info = f" [{chunk.symbol_type}: {chunk.symbol_name}]" if chunk.symbol_name else ""
        header = (
            f"=== Code Chunk {i}/{len(chunks)} ===\n"
            f"File: {chunk.file_path} (lines {chunk.start_line}–{chunk.end_line}){symbol_info}\n"
            f"Language: {chunk.language}\n"
        )
        context_parts.append(header + chunk.content)

    context = "\n\n".join(context_parts)

    # Build conversation history section (injected between code context and current question)
    history_section = ""
    if conversation_history:
        # Take last N turns (capped at _MAX_HISTORY_TURNS pairs = 2*N messages)
        recent = conversation_history[-(2 * _MAX_HISTORY_TURNS):]
        history_parts = []
        for msg in recent:
            role_label = "User" if msg["role"] == "user" else "RepoMind"
            content = msg["content"]
            # Truncate long assistant answers in history to save tokens
            if msg["role"] == "assistant" and len(content) > 600:
                content = content[:600] + "... [truncated for brevity]"
            history_parts.append(f"**{role_label}**: {content}")
        history_section = (
            "## Prior Conversation\n\n"
            + "\n\n".join(history_parts)
            + "\n\n---\n\n"
        )

    prompt = (
        f"## Repository Code Context\n\n"
        f"{context}\n\n"
        f"---\n\n"
        f"{history_section}"
        f"## Question\n\n"
        f"{question}\n\n"
        f"## Answer\n\n"
        f"Based on the code context above, answer the question. "
        f"Reference specific files and functions where relevant."
    )

    return prompt


def build_citations(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    """
    Build structured citation objects from retrieved chunk metadata.
    Citations are NEVER derived from LLM-generated text.
    """
    return [
        {
            "file_path": chunk.file_path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "content": chunk.content,
            "symbol_name": chunk.symbol_name,
            "symbol_type": chunk.symbol_type,
            "language": chunk.language,
            "similarity": round(chunk.similarity, 4),
        }
        for chunk in chunks
    ]


async def stream_rag_answer(
    question: str,
    chunks: list[RetrievedChunk],
    conversation_history: list[dict] | None = None,
) -> AsyncIterator[str]:
    """
    Stream LLM tokens for the given question + retrieved chunks + optional history.

    Args:
        question: The user's current question.
        chunks: Retrieved and (optionally) reranked code chunks.
        conversation_history: List of prior {role, content} dicts for this conversation.

    Yields:
        String tokens as they arrive from the LLM.
    """
    from app.ai.llm_client import get_llm_client, LLMError

    prompt = build_prompt(question, chunks, conversation_history=conversation_history)
    llm = get_llm_client()

    gen_start = time.time()
    try:
        async for token in llm.generate(prompt, stream=True, system_prompt=_RAG_SYSTEM_PROMPT):
            yield token

        logger.info(
            "LLM generation complete",
            extra={"latency_s": round(time.time() - gen_start, 2)},
        )
    except LLMError as e:
        logger.error("LLM generation failed", extra={"error": str(e)})
        raise


async def delete_chunks_for_files(
    db: AsyncSession,
    repo_id: uuid.UUID,
    file_paths: list[str],
) -> int:
    """
    Delete code chunks (and their embeddings, via cascade) for specific files.
    Used during incremental re-indexing to remove stale data before re-processing.

    Returns:
        Number of rows deleted from the files table.
    """
    from sqlalchemy import text

    if not file_paths:
        return 0

    sql = text("""
        DELETE FROM files
        WHERE repository_id = :repo_id
          AND path = ANY(:paths)
    """)
    result = await db.execute(sql, {"repo_id": str(repo_id), "paths": file_paths})
    await db.flush()
    deleted = result.rowcount
    logger.info(
        "Incremental re-index: deleted stale file chunks",
        extra={"repo_id": str(repo_id), "files_deleted": deleted},
    )
    return deleted
