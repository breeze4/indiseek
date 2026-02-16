"""LLM provider interface and Gemini implementation."""

from __future__ import annotations

import logging
import time
from typing import Protocol

from google import genai
from google.genai import types

from indiseek import config

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, returning a list of float vectors."""
        ...


class GenerationProvider(Protocol):
    """Protocol for text generation providers."""

    def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate text from a prompt, returning the response string."""
        ...


class GeminiProvider:
    """Gemini implementation of embedding (and later, generation)."""

    def __init__(
        self,
        api_key: str | None = None,
        embedding_model: str | None = None,
        embedding_dims: int | None = None,
        generation_model: str | None = None,
    ) -> None:
        self._client = genai.Client(api_key=api_key or config.GEMINI_API_KEY)
        self._embedding_model = embedding_model or config.EMBEDDING_MODEL
        self._embedding_dims = embedding_dims or config.EMBEDDING_DIMS
        self._generation_model = generation_model or config.GEMINI_MODEL

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using Gemini embedding API.

        Args:
            texts: List of strings to embed. Max 250 per call.

        Returns:
            List of float vectors, one per input text.
        """
        total_chars = sum(len(t) for t in texts)
        logger.debug("Embedding %d text(s) (%d chars) via %s", len(texts), total_chars, self._embedding_model)
        t0 = time.perf_counter()
        result = self._client.models.embed_content(
            model=self._embedding_model,
            contents=texts,
            config=types.EmbedContentConfig(
                output_dimensionality=self._embedding_dims,
            ),
        )
        logger.debug("Embed complete: %.0fms", (time.perf_counter() - t0) * 1000)
        return [e.values for e in result.embeddings]

    def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate text using Gemini.

        Args:
            prompt: The user prompt.
            system: Optional system instruction.

        Returns:
            The generated text response.
        """
        logger.debug("Generate via %s (%d char prompt)", self._generation_model, len(prompt))
        t0 = time.perf_counter()
        gen_config = None
        if system:
            gen_config = types.GenerateContentConfig(
                system_instruction=system,
            )
        response = self._client.models.generate_content(
            model=self._generation_model,
            contents=prompt,
            config=gen_config,
        )
        logger.debug("Generate complete: %d chars, %.0fms", len(response.text or ""), (time.perf_counter() - t0) * 1000)
        return response.text
