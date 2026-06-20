"""
LLMClient — abstract interface and provider factory.

All LLM generation calls in the application go through this interface.
To swap providers (Gemini → OpenAI/Anthropic/Ollama), change LLM_PROVIDER in
the environment; no application code outside this module needs to change.

Interface contract:
    generate(prompt: str, stream: bool = True) -> AsyncIterator[str]
        - Yields text tokens as they arrive (when stream=True).
        - When stream=False, yields a single string with the full response.
        - Raises LLMError on failure.

Future providers this interface must accommodate (not implemented now):
    - OpenAI (chat completions streaming)
    - Anthropic (messages streaming)
    - Ollama (local LLM, /api/generate streaming)
"""

from __future__ import annotations

import abc
import logging
from collections.abc import AsyncIterator
from functools import lru_cache

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when an LLM API call fails."""
    pass


class LLMClient(abc.ABC):
    """
    Abstract LLM client.

    Implementations must:
      - Return an async generator even for non-streaming calls (yield once).
      - Never expose provider-specific types to callers.
      - Raise LLMError (not raw provider exceptions) on failure.
    """

    @abc.abstractmethod
    async def generate(
        self,
        prompt: str,
        stream: bool = True,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Generate a response for the given prompt.

        Args:
            prompt: The user-facing prompt / question with context.
            stream: If True, yield tokens as they arrive. If False, yield the
                    complete response as a single string.
            system_prompt: Optional system-level instruction.

        Yields:
            String tokens (or one full string when stream=False).

        Raises:
            LLMError: On any generation failure.
        """
        ...


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """
    Factory: returns the configured LLMClient singleton.
    Provider is selected by the LLM_PROVIDER environment variable.
    """
    from app.config import get_settings

    settings = get_settings()
    provider = settings.llm_provider

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise LLMError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set. "
                "Please set GEMINI_API_KEY in your environment."
            )
        from app.ai.llm_providers.gemini import GeminiLLMClient
        client = GeminiLLMClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )
        logger.info(
            "LLM client initialised",
            extra={"provider": "gemini", "model": settings.gemini_model},
        )
        return client

    else:
        raise LLMError(
            f"Unknown LLM_PROVIDER={provider!r}. "
            "Currently only 'gemini' is implemented."
        )
