"""
Gemini LLM provider implementation using the google-genai SDK.

All Gemini-specific types, shapes, and API calls are confined to this module.
Callers interact only with the LLMClient interface.

SDK: google-genai (NOT the deprecated google-generativeai)
Model: Configurable via GEMINI_MODEL env var (default: gemini-2.5-flash)

Verify current free-tier model availability at:
  https://ai.google.dev/gemini-api/docs/models
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.ai.llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_DEFAULT = (
    "You are RepoMind, an expert code assistant. "
    "You answer questions about a specific software repository using only the code "
    "context provided. Be precise, cite the relevant code, and acknowledge when "
    "something cannot be determined from the provided context. "
    "Never make up code, file paths, or behavior that isn't shown in the context."
)


class GeminiLLMClient(LLMClient):
    """
    LLM client backed by Google Gemini via the google-genai SDK.

    Streaming is implemented by running the blocking Gemini streaming call in a
    thread pool (via asyncio.to_thread) because the google-genai SDK does not
    yet provide a native async streaming interface at the chunk level.
    """

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._api_key = api_key
        self._model = model
        self._client = None  # lazy init

    def _get_client(self):
        """Lazily initialize the Gemini client."""
        if self._client is None:
            try:
                from google import genai  # google-genai SDK
                self._client = genai.Client(api_key=self._api_key)
            except ImportError as e:
                raise LLMError(
                    "google-genai package is not installed. "
                    "Install it with: pip install google-genai"
                ) from e
        return self._client

    async def generate(
        self,
        prompt: str,
        stream: bool = True,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Generate a response via Gemini, optionally streaming.

        This method is an async generator — callers must use `async for` to
        consume tokens, even when stream=False (it will yield once).
        """
        client = self._get_client()
        effective_system = system_prompt or _SYSTEM_PROMPT_DEFAULT

        if stream:
            async for token in self._stream(client, prompt, effective_system):
                yield token
        else:
            result = await self._generate_full(client, prompt, effective_system)
            yield result

    async def _stream(
        self, client, prompt: str, system_prompt: str
    ) -> AsyncIterator[str]:
        """Stream response tokens from Gemini."""
        try:
            from google.genai import types as genai_types

            contents = prompt
            config = genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
            )

            # Gemini SDK streaming is synchronous; run in thread pool
            def _blocking_stream():
                return client.models.generate_content_stream(
                    model=self._model,
                    contents=contents,
                    config=config,
                )

            loop = asyncio.get_event_loop()
            stream_iter = await loop.run_in_executor(None, _blocking_stream)

            # Iterate over streaming chunks, yield each text fragment
            for chunk in stream_iter:
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            logger.error(
                "Gemini streaming generation failed",
                extra={"model": self._model, "error": str(e)},
            )
            raise LLMError(
                f"Gemini API streaming call failed: {e}. "
                "Check your GEMINI_API_KEY and GEMINI_MODEL settings."
            ) from e

    async def _generate_full(
        self, client, prompt: str, system_prompt: str
    ) -> str:
        """Non-streaming full response from Gemini."""
        try:
            from google.genai import types as genai_types

            config = genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
            )

            def _blocking_call():
                return client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=config,
                )

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, _blocking_call)
            return response.text or ""

        except Exception as e:
            raise LLMError(
                f"Gemini API call failed: {e}. "
                "Check your GEMINI_API_KEY and GEMINI_MODEL settings."
            ) from e
