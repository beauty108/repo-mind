"""
Chat API endpoints.

POST  /api/repos/{id}/chat
    — Send a message, streams SSE response tokens + final citations.

GET   /api/repos/{id}/conversations/{conv_id}/messages
    — Retrieve full chat history for a conversation.

DELETE /api/repos/{id}/chat/stream/{stream_id}
    — Cancel an active streaming response.

SSE Event format:
    data: {"type": "token", "content": "<text>"}
    data: {"type": "done", "citations": [...], "conversation_id": "...", "message_id": "..."}
    data: {"type": "error", "detail": "<message>"}
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_db
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.repository import Repository, RepositoryStatus
from app.schemas.conversation import ChatRequest, ConversationResponse
from app.schemas.message import ConversationMessagesResponse, MessageResponse, CitationSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos", tags=["chat"])


@router.post(
    "/{repo_id}/chat",
    summary="Ask a question about the repository (SSE streaming)",
    description=(
        "Send a natural-language question. Response is a Server-Sent Events (SSE) "
        "stream of tokens, followed by a 'done' event with structured citations. "
        "Pass conversation_id to maintain conversation memory across turns."
    ),
    response_class=EventSourceResponse,
)
async def chat(
    repo_id: uuid.UUID,
    body: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # 1. Verify repo exists and is ready
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    if repo.status != RepositoryStatus.ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Repository is not ready (status: {repo.status.value}). Wait for indexing to complete.",
        )

    # 2. Validate model consistency
    from app.services.rag import validate_model_consistency
    try:
        await validate_model_consistency(db, repo_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    # 3. Resolve or create conversation
    conversation = await _get_or_create_conversation(db, repo_id, body.conversation_id)
    conv_id = conversation.id

    # 4. Load conversation history (for memory)
    conversation_history = await _load_conversation_history(db, conv_id)

    # 5. Save user message
    user_msg = Message(
        conversation_id=conv_id,
        role=MessageRole.user,
        content=body.message,
    )
    db.add(user_msg)
    await db.commit()

    # 6. Sanitize question
    question = body.message.strip()

    # 8. Generate a unique stream ID for cancellation support
    stream_id = str(uuid.uuid4())

    # 9. Build the full RAG pipeline
    from app.config import get_settings
    from app.services.rag import (
        embed_query,
        retrieve_chunks_hybrid,
        retrieve_chunks,
        build_citations,
        stream_rag_answer,
    )
    from app.ai.llm_client import LLMError

    settings = get_settings()

    async def sse_generator() -> AsyncIterator[str]:
        answer_parts: list[str] = []
        citations: list[dict] = []
        asst_msg_id: uuid.UUID | None = None

        try:
            import time
            start_time = time.time()
            
            # Embed query (sync, run in thread pool)
            import asyncio
            query_vector = await asyncio.get_event_loop().run_in_executor(
                None, embed_query, question
            )

            # Check semantic cache here!
            from app.services.cache import get_cached_chat_semantic, set_cached_chat_semantic
            cached = get_cached_chat_semantic(str(repo_id), repo.indexed_commit_sha, query_vector)

            if cached:
                cached_answer, cached_citations = cached
                # Save assistant message from cache
                async with db.begin_nested():
                    asst_msg = await _save_assistant_message(db, conv_id, cached_answer, cached_citations)
                    cached_asst_msg_id = asst_msg.id
                await db.commit()
                logger.info("Serving semantic cached chat response", extra={"repo_id": str(repo_id)})

                # Emit cached tokens in chunks (simulate streaming for frontend compatibility)
                yield json.dumps({
                    "type": "started",
                    "stream_id": stream_id,
                })
                chunk_size = 50
                for i in range(0, len(cached_answer), chunk_size):
                    chunk = cached_answer[i : i + chunk_size]
                    yield json.dumps({"type": "token", "content": chunk})
                yield json.dumps({
                    "type": "done",
                    "citations": cached_citations,
                    "conversation_id": str(conv_id),
                    "message_id": str(cached_asst_msg_id),
                    "stream_id": stream_id,
                })
                return

            # Retrieve chunks — use hybrid search if enabled
            try:
                chunks = await retrieve_chunks_hybrid(
                    db, repo_id, query_vector, question, top_k=settings.top_k
                )
            except Exception as retrieval_err:
                logger.warning(
                    "Hybrid retrieval failed, falling back to vector-only",
                    extra={"error": str(retrieval_err)},
                )
                chunks = await retrieve_chunks(db, repo_id, query_vector, top_k=settings.top_k)

            # Store the raw chunks for tracing
            raw_retrieved_chunks = list(chunks)

            if not chunks:
                error_msg = (
                    "No relevant code was found for your question. "
                    "The repository may not contain code related to this topic."
                )
                yield json.dumps({"type": "error", "detail": error_msg})
                return

            # Apply reranking if enabled
            try:
                from app.ai.reranker_client import get_reranker_client
                reranker = get_reranker_client()
                chunks = reranker.rerank(question, chunks)
            except Exception as rerank_err:
                logger.warning(
                    "Reranking failed, using retrieval order",
                    extra={"error": str(rerank_err)},
                )

            # Build citations from top context chunks
            context_chunks = chunks[:settings.max_context_chunks]
            citations = build_citations(context_chunks)

            # Stream LLM response with conversation history
            from app.services.cache import is_stream_cancelled

            # Emit initial event to pass the stream_id back to the frontend
            yield json.dumps({
                "type": "started",
                "stream_id": stream_id,
            })

            async for token in stream_rag_answer(
                question,
                context_chunks,
                conversation_history=conversation_history,
            ):
                if is_stream_cancelled(stream_id):
                    break
                answer_parts.append(token)
                yield json.dumps({"type": "token", "content": token})

            full_answer = "".join(answer_parts)

            # Save assistant message with citations
            async with db.begin_nested():
                asst_msg = await _save_assistant_message(db, conv_id, full_answer, citations)
                asst_msg_id = asst_msg.id
            await db.commit()

            # Cache the response
            set_cached_chat_semantic(str(repo_id), repo.indexed_commit_sha, question, query_vector, full_answer, citations)

            # Emit Langfuse trace
            from app.services.tracing import trace_rag_call
            trace_rag_call(
                repo_id=str(repo_id),
                query=question,
                retrieved_chunks=raw_retrieved_chunks,
                reranked_chunks=context_chunks,
                answer=full_answer,
                latency_s=time.time() - start_time,
                conversation_id=str(conv_id)
            )

            # Emit final done event with citations
            yield json.dumps({
                "type": "done",
                "citations": citations,
                "conversation_id": str(conv_id),
                "message_id": str(asst_msg_id) if asst_msg_id else None,
                "stream_id": stream_id,
            })

        except LLMError as e:
            logger.error("LLM error in chat endpoint", extra={"error": str(e)})
            yield json.dumps({
                "type": "error",
                "detail": "The AI model encountered an error generating a response. Please try again.",
            })
        except ValueError as e:
            logger.error("Retrieval error in chat endpoint", extra={"error": str(e)})
            yield json.dumps({
                "type": "error",
                "detail": str(e),
            })
        except Exception as e:
            logger.exception("Unexpected error in chat endpoint", extra={"error": str(e)})
            yield json.dumps({
                "type": "error",
                "detail": "An unexpected error occurred. Please try again.",
            })

    return EventSourceResponse(sse_generator())


@router.delete(
    "/{repo_id}/chat/stream/{stream_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel an active streaming response",
)
async def cancel_stream(
    repo_id: uuid.UUID,
    stream_id: str,
):
    """
    Signal the server to stop an active SSE stream.
    The stream generator checks this flag between token yields.
    """
    from app.services.cache import set_stream_cancelled
    set_stream_cancelled(stream_id)
    return


@router.get(
    "/{repo_id}/conversations/{conv_id}/messages",
    response_model=ConversationMessagesResponse,
    summary="Get chat history for a conversation",
)
async def get_messages(
    repo_id: uuid.UUID,
    conv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    # Verify conversation belongs to this repo
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conv_id,
            Conversation.repository_id == repo_id,
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Fetch messages ordered by creation time
    msgs_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at)
    )
    messages = msgs_result.scalars().all()

    return ConversationMessagesResponse(
        conversation_id=conv_id,
        repository_id=repo_id,
        messages=[
            MessageResponse(
                id=m.id,
                conversation_id=m.conversation_id,
                role=m.role.value,
                content=m.content,
                citations=(
                    [CitationSchema(**c) for c in m.citations]
                    if m.citations
                    else None
                ),
                created_at=m.created_at,
            )
            for m in messages
        ],
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_create_conversation(
    db: AsyncSession,
    repo_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
) -> Conversation:
    """Return an existing conversation or create a new one."""
    if conversation_id is not None:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.repository_id == repo_id,
            )
        )
        conv = result.scalar_one_or_none()
        if conv is not None:
            return conv
        # Requested conversation not found — create a new one
        logger.warning(
            "Conversation not found, creating new one",
            extra={"conversation_id": str(conversation_id), "repo_id": str(repo_id)},
        )

    conv = Conversation(repository_id=repo_id)
    db.add(conv)
    await db.flush()
    return conv


async def _load_conversation_history(
    db: AsyncSession,
    conv_id: uuid.UUID,
    max_messages: int = 6,
) -> list[dict]:
    """
    Load the last N messages from a conversation for history injection.

    Returns a list of {role, content} dicts ordered oldest-first.
    """
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.desc())
        .limit(max_messages)
    )
    messages = result.scalars().all()

    # Reverse to get chronological order (oldest first)
    return [
        {"role": m.role.value, "content": m.content}
        for m in reversed(messages)
    ]


async def _save_assistant_message(
    db: AsyncSession,
    conv_id: uuid.UUID,
    content: str,
    citations: list[dict],
) -> Message:
    """Persist the assistant's response with structured citations."""
    msg = Message(
        conversation_id=conv_id,
        role=MessageRole.assistant,
        content=content,
        citations=citations,
    )
    db.add(msg)
    await db.flush()
    return msg
