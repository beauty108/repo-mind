"""
Local embedding provider using BAAI/bge-small-en-v1.5 via sentence-transformers.

Model properties:
  - Embedding dimension: 384
  - Effective context window: 512 tokens
  - Size: ~130MB on disk
  - Inference: CPU-capable, GPU optional

The model is lazy-loaded on first embed() call. If the model weights are not
cached locally and there is no network access, this will raise EmbeddingError
with a clear message rather than silently timing out.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.ai.embedding_client import EmbeddingClient, EmbeddingError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Maximum tokens the BGE-small model handles well
_BGE_MAX_TOKENS = 512
# Batch size for encoding — tuned for CPU throughput with this model size
_ENCODE_BATCH_SIZE = 64


class LocalBGEEmbeddingClient(EmbeddingClient):
    """
    Embedding client backed by a local sentence-transformers model.
    Thread-safe after first load (SentenceTransformer.encode is thread-safe
    when called with the same model instance).
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model_name
        self._model = None  # lazy load

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load_model(self):
        """Load the sentence-transformers model, raising EmbeddingError on failure."""
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise EmbeddingError(
                "sentence-transformers is not installed. "
                "Install it with: pip install sentence-transformers"
            ) from e

        try:
            logger.info(
                "Loading local embedding model",
                extra={"model": self._model_name},
            )
            # local_files_only=False allows download on first use, but if no
            # network is available and the model isn't cached, this will raise
            # an OSError that we convert to a clear EmbeddingError below.
            # Force CPU device to avoid MPS deadlocks/slowness inside Celery forks on macOS.
            model = SentenceTransformer(self._model_name, device="cpu")
            self._model = model
            logger.info(
                "Local embedding model loaded",
                extra={"model": self._model_name},
            )
            return model
        except OSError as e:
            raise EmbeddingError(
                f"Failed to load local embedding model '{self._model_name}'. "
                "If the model has not been downloaded yet, ensure network access "
                "is available for the first run, or pre-download it by running: "
                f"  python -c \"from sentence_transformers import SentenceTransformer; "
                f"SentenceTransformer('{self._model_name}')\"\n"
                f"Original error: {e}"
            ) from e
        except Exception as e:
            raise EmbeddingError(
                f"Unexpected error loading embedding model '{self._model_name}': {e}"
            ) from e

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed texts in batches. Returns normalised float vectors.

        BGE models are trained with query prefix "Represent this sentence: ";
        for code chunks (passages) no prefix is needed. We use the raw text.
        """
        if not texts:
            return []

        model = self._load_model()

        try:
            import numpy as np

            all_embeddings: list[list[float]] = []
            for i in range(0, len(texts), _ENCODE_BATCH_SIZE):
                batch = texts[i : i + _ENCODE_BATCH_SIZE]
                # normalize_embeddings=True ensures unit vectors for cosine similarity
                vectors = model.encode(
                    batch,
                    batch_size=_ENCODE_BATCH_SIZE,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                all_embeddings.extend(vectors.tolist())

            return all_embeddings

        except Exception as e:
            raise EmbeddingError(
                f"Error during local embedding inference: {e}"
            ) from e
