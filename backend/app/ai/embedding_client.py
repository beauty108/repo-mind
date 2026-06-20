"""
EmbeddingClient — abstract interface and provider factory.

All embedding calls in the application go through this interface.
To swap providers, change EMBEDDING_PROVIDER in the environment; no application
code outside this module needs to change.

Interface contract:
    embed(texts: list[str]) -> list[list[float]]
        - Takes a list of strings (any length).
        - Returns a list of float vectors (one per input, same order).
        - Batches internally; callers need not worry about batch sizing.
        - Raises EmbeddingError on failure.
"""

from __future__ import annotations

import abc
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Raised when an embedding call fails (network, model not loaded, etc.)."""
    pass


class EmbeddingClient(abc.ABC):
    """
    Abstract embedding client.

    Concrete implementations must:
      - Be safe to call from multiple threads (Celery worker may use threads).
      - Implement internal batching — callers may pass arbitrarily large lists.
      - Raise EmbeddingError (not raw provider exceptions) on failure.
    """

    @property
    @abc.abstractmethod
    def model_name(self) -> str:
        """Canonical model identifier stored in the Embeddings table."""
        ...

    @abc.abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts and return their float vectors.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            List of float vectors, same length and order as `texts`.

        Raises:
            EmbeddingError: On any failure.
        """
        ...


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    """
    Factory: returns the configured EmbeddingClient singleton.
    Provider is selected by the EMBEDDING_PROVIDER environment variable.
    The instance is cached — the expensive model load happens once.
    """
    from app.config import get_settings

    settings = get_settings()
    provider = settings.embedding_provider

    if provider == "local":
        from app.ai.providers.local_bge import LocalBGEEmbeddingClient
        client = LocalBGEEmbeddingClient(model_name=settings.local_embedding_model)
        logger.info(
            "Embedding client initialised",
            extra={"provider": "local", "model": settings.local_embedding_model},
        )
        return client

    elif provider == "openai":
        if not settings.openai_api_key:
            raise EmbeddingError(
                "EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set. "
                "Please set OPENAI_API_KEY in your environment."
            )
        from app.ai.providers.openai_embed import OpenAIEmbeddingClient
        client = OpenAIEmbeddingClient(api_key=settings.openai_api_key)
        logger.info(
            "Embedding client initialised", extra={"provider": "openai"}
        )
        return client

    else:
        raise EmbeddingError(
            f"Unknown EMBEDDING_PROVIDER={provider!r}. "
            "Valid options: 'local', 'openai'."
        )
