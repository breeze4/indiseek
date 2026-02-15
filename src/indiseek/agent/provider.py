"""LLM provider interface and Gemini implementation."""

from __future__ import annotations

from typing import Protocol

from google import genai
from google.genai import types

from indiseek import config


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
        result = self._client.models.embed_content(
            model=self._embedding_model,
            contents=texts,
            config=types.EmbedContentConfig(
                output_dimensionality=self._embedding_dims,
            ),
        )
        return [e.values for e in result.embeddings]

    def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate text using Gemini.

        Args:
            prompt: The user prompt.
            system: Optional system instruction.

        Returns:
            The generated text response.
        """
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
        return response.text
