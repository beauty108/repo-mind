"""
OpenAI embedding provider using text-embedding-3-small.

Model properties:
  - Embedding dimension: 1536 (default; can be reduced via 'dimensions' param)
  - Max input: 8191 tokens per text
  - API: openai.embeddings.create()

IMPORTANT: OpenAI embeddings have 1536 dimensions vs BGE's 384.
Switching providers on an existing repository requires a full re-index.
The model_name stored in Embeddings will be "text-embedding-3-small",
which is checked at query time to prevent cross-provider retrieval.
"""

from __future__ import annotations

import logging

from app.ai.embedding_client import EmbeddingClient, EmbeddingError

logger = logging.getLogger(__name__)

_MODEL_NAME = "text-embedding-3-small"
# OpenAI allows up to 2048 items per batch
_OPENAI_BATCH_SIZE = 2048


class OpenAIEmbeddingClient(EmbeddingClient):
    """Embedding client backed by OpenAI's text-embedding-3-small API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = None  # lazy init

    @property
    def model_name(self) -> str:
        return _MODEL_NAME

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self._api_key)
            except ImportError as e:
                raise EmbeddingError(
                    "openai package is not installed. Install it with: pip install openai"
                ) from e
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._get_client()
        all_embeddings: list[list[float]] = []

        try:
            for i in range(0, len(texts), _OPENAI_BATCH_SIZE):
                batch = texts[i : i + _OPENAI_BATCH_SIZE]
                response = client.embeddings.create(
                    model=_MODEL_NAME,
                    input=batch,
                )
                # Response items are ordered by index
                sorted_items = sorted(response.data, key=lambda x: x.index)
                all_embeddings.extend(item.embedding for item in sorted_items)

            return all_embeddings

        except Exception as e:
            raise EmbeddingError(
                f"OpenAI embedding API call failed: {e}"
            ) from e
