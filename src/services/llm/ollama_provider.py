"""Ollama LLM provider extending OpenAIProvider with model discovery."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from src.services.llm.openai_provider import OpenAIProvider
from src.services.llm.provider import ModelInfo

logger = logging.getLogger(__name__)

# Model families known to support tool/function calling.
_TOOL_CAPABLE_FAMILIES: frozenset[str] = frozenset({
    "llama",
    "mistral",
    "qwen",
    "command-r",
    "gemma",
})


class OllamaProvider(OpenAIProvider):
    """LLM provider for Ollama, which exposes an OpenAI-compatible API.

    Ollama serves models at ``{host}/v1/`` using the same Chat Completions
    format as OpenAI.  The only Ollama-specific functionality is model
    discovery via the native ``/api/tags`` endpoint.

    Usage::

        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        provider = OllamaProvider(client=client, model="llama3.1:8b")
    """

    def __init__(
        self,
        client: Any,
        model: str,
        supports_tools: bool = False,
    ) -> None:
        super().__init__(client=client, model=model, supports_tools=supports_tools)

    # -- Protocol properties (override) ----------------------------------------

    @property
    def provider_name(self) -> str:
        return "ollama"

    # -- Ollama-specific model discovery ---------------------------------------

    @staticmethod
    async def discover_models(
        host: str,
        session: aiohttp.ClientSession,
    ) -> list[ModelInfo]:
        """Query an Ollama instance for available models.

        Calls ``GET {host}/api/tags`` and returns a list of ``ModelInfo``
        objects.  Tool support is inferred from the model's family field.

        Args:
            host: Ollama base URL, e.g. ``http://localhost:11434``.
            session: An ``aiohttp.ClientSession`` to use for the request.

        Returns:
            List of discovered models, or empty list on any error.
        """
        url = f"{host.rstrip('/')}/api/tags"

        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Ollama model discovery returned status %d", resp.status
                    )
                    return []

                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.debug("Ollama model discovery failed: %s", exc)
            return []

        models: list[ModelInfo] = []
        for entry in data.get("models", []):
            model_id = entry.get("name", entry.get("model", "unknown"))
            family = entry.get("details", {}).get("family", "")
            supports_tools = family in _TOOL_CAPABLE_FAMILIES

            models.append(
                ModelInfo(
                    id=model_id,
                    name=model_id,
                    provider="ollama",
                    supports_tools=supports_tools,
                )
            )

        return models
