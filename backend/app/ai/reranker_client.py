"""
Reranker client — cross-encoder reranking pipeline.

The reranker sits between retrieval and generation:
    Query → Vector (top-20) + BM25 (top-20) → RRF merge → Reranker → top-5 → LLM

Cross-encoders score each (query, chunk) pair jointly, dramatically improving
precision over pure vector similarity which can return semantically close but
contextually irrelevant results.

Implementations:
  - LocalBGEReranker: BAAI/bge-reranker-base (local, ~500MB, no API cost)
  - NoOpReranker: pass-through when reranking is disabled (identity function)

The reranker is loaded once and cached via lru_cache.
"""

from __future__ import annotations

import abc
import logging
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.rag import RetrievedChunk

logger = logging.getLogger(__name__)


class RerankerClient(abc.ABC):
    """Abstract cross-encoder reranker interface."""

    @abc.abstractmethod
    def rerank(
        self,
        query: str,
        chunks: list["RetrievedChunk"],
    ) -> list["RetrievedChunk"]:
        """
        Rerank a list of retrieved chunks by relevance to the query.

        Args:
            query: The user's question.
            chunks: Candidate chunks to rerank (from vector + BM25 retrieval).

        Returns:
            Chunks sorted by reranker score descending.
            May return fewer chunks than input (trimmed to reranker_top_k).
        """
        ...


class NoOpReranker(RerankerClient):
    """Pass-through reranker — returns chunks in their original order."""

    def rerank(self, query: str, chunks: list["RetrievedChunk"]) -> list["RetrievedChunk"]:
        return chunks


class LocalBGEReranker(RerankerClient):
    """
    Local cross-encoder reranker using BAAI/bge-reranker-base (or similar model).

    Uses HuggingFace sentence-transformers CrossEncoder under the hood.
    The model is loaded once and cached (first call may take a few seconds).
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-base", top_k: int = 5):
        self._model_name = model_name
        self._top_k = top_k
        self._model = None  # Lazy load on first call

    def _get_model(self):
        """Lazy-load the cross-encoder model (cached after first call)."""
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                logger.info(
                    "Loading reranker model (this may take a moment on first run)",
                    extra={"model": self._model_name},
                )
                self._model = CrossEncoder(self._model_name, max_length=512)
                logger.info("Reranker model loaded successfully", extra={"model": self._model_name})
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers is required for reranking. "
                    "Install with: pip install sentence-transformers"
                )
            except Exception as e:
                raise RuntimeError(f"Failed to load reranker model '{self._model_name}': {e}") from e
        return self._model

    def rerank(self, query: str, chunks: list["RetrievedChunk"]) -> list["RetrievedChunk"]:
        """Score each (query, chunk_content) pair and re-sort by cross-encoder score."""
        if not chunks:
            return chunks

        try:
            model = self._get_model()

            # Build (query, passage) pairs for the cross-encoder
            pairs = [(query, chunk.content) for chunk in chunks]

            # Score all pairs in one batch
            scores = model.predict(pairs, show_progress_bar=False)

            # Attach scores and sort
            scored = sorted(
                zip(scores, chunks),
                key=lambda x: float(x[0]),
                reverse=True,
            )

            # Trim to top_k and strip scores
            result = [chunk for _, chunk in scored[: self._top_k]]

            logger.info(
                "Reranking complete",
                extra={
                    "model": self._model_name,
                    "input_chunks": len(chunks),
                    "output_chunks": len(result),
                },
            )

            return result

        except Exception as e:
            logger.error(
                "Reranking failed — falling back to original order",
                extra={"model": self._model_name, "error": str(e)},
            )
            # Graceful degradation: return original order trimmed to top_k
            return chunks[: self._top_k]


@lru_cache(maxsize=1)
def get_reranker_client() -> RerankerClient:
    """
    Factory: returns the configured RerankerClient singleton.
    Settings control which implementation is used and the final top-k.
    """
    from app.config import get_settings

    settings = get_settings()

    if not settings.reranker_enabled:
        logger.info("Reranking disabled (RERANKER_ENABLED=false)")
        return NoOpReranker()

    try:
        client = LocalBGEReranker(
            model_name=settings.reranker_model,
            top_k=settings.reranker_top_k,
        )
        logger.info(
            "Reranker client initialized",
            extra={
                "model": settings.reranker_model,
                "top_k": settings.reranker_top_k,
            },
        )
        return client
    except Exception as e:
        logger.warning(
            "Failed to initialize reranker, using NoOp fallback",
            extra={"error": str(e)},
        )
        return NoOpReranker()
