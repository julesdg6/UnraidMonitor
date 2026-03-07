"""LLM provider protocol and shared data types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """A normalized tool call from any LLM provider."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""
    text: str
    stop_reason: str  # "end", "tool_use", "max_tokens"
    tool_calls: list[ToolCall] | None = None


@dataclass
class ModelInfo:
    """Information about an available model."""
    id: str
    name: str
    provider: str
    supports_tools: bool = True


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    @property
    def supports_tools(self) -> bool: ...

    @property
    def model_name(self) -> str: ...

    @property
    def provider_name(self) -> str: ...
