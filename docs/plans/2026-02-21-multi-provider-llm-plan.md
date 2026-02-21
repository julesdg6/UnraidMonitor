# Multi-Provider LLM Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add OpenAI and Ollama provider support alongside Anthropic, with a provider abstraction layer, `/model` command for runtime switching, and graceful degradation for providers without tool-calling support.

**Architecture:** A `LLMProvider` protocol defines a normalized interface (`chat()` returning `LLMResponse`). Three implementations (Anthropic, OpenAI, Ollama) translate between the normalized format and each SDK. A `ProviderRegistry` manages available providers and resolves which one to use. All 4 AI consumers switch from raw `anthropic_client` to `LLMProvider`.

**Tech Stack:** Python 3.11, anthropic SDK, openai SDK (new), aiohttp (for Ollama model discovery), pytest-asyncio

---

### Task 1: Add openai dependency

**Files:**
- Modify: `requirements.txt`

**Step 1: Add openai to requirements.txt**

Add `openai>=1.50.0,<2.0.0` to `requirements.txt` after the anthropic line. The openai SDK is used for both OpenAI and Ollama (OpenAI-compatible API).

```
openai>=1.50.0,<2.0.0
```

**Step 2: Install the dependency**

Run: `pip install -r requirements.txt`
Expected: Successfully installed openai

**Step 3: Verify import works**

Run: `python -c "import openai; print(openai.__version__)"`
Expected: Version number prints without error

**Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: add openai SDK for multi-provider LLM support"
```

---

### Task 2: Create LLM provider protocol and data types

**Files:**
- Create: `src/services/llm/__init__.py`
- Create: `src/services/llm/provider.py`
- Test: `tests/test_llm_provider.py`

**Step 1: Write the failing test**

```python
# tests/test_llm_provider.py
"""Tests for LLM provider protocol and data types."""

from src.services.llm.provider import LLMResponse, ToolCall, ModelInfo


class TestLLMResponse:
    def test_text_response(self):
        response = LLMResponse(text="Hello", stop_reason="end")
        assert response.text == "Hello"
        assert response.tool_calls is None
        assert response.stop_reason == "end"

    def test_tool_use_response(self):
        calls = [ToolCall(id="1", name="get_status", input={"name": "plex"})]
        response = LLMResponse(text="", tool_calls=calls, stop_reason="tool_use")
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "get_status"
        assert response.stop_reason == "tool_use"


class TestToolCall:
    def test_creation(self):
        tc = ToolCall(id="call_123", name="restart_container", input={"name": "plex"})
        assert tc.id == "call_123"
        assert tc.name == "restart_container"
        assert tc.input == {"name": "plex"}


class TestModelInfo:
    def test_creation(self):
        info = ModelInfo(
            id="gpt-4o",
            name="GPT-4o",
            provider="openai",
            supports_tools=True,
        )
        assert info.id == "gpt-4o"
        assert info.provider == "openai"
        assert info.supports_tools is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_provider.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write the implementation**

```python
# src/services/llm/__init__.py
"""Multi-provider LLM abstraction layer."""

from src.services.llm.provider import LLMProvider, LLMResponse, ToolCall, ModelInfo

__all__ = ["LLMProvider", "LLMResponse", "ToolCall", "ModelInfo"]
```

```python
# src/services/llm/provider.py
"""LLM provider protocol and shared data types."""

from __future__ import annotations

from dataclasses import dataclass, field
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

    id: str  # Model ID used in API calls (e.g. "gpt-4o", "claude-haiku-4-5-20251001")
    name: str  # Display name (e.g. "GPT-4o", "Claude Haiku")
    provider: str  # Provider key (e.g. "anthropic", "openai", "ollama")
    supports_tools: bool = True


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers.

    Each provider translates between this normalized interface and its
    native SDK format. Consumers use this protocol instead of any
    provider-specific client.
    """

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of normalized message dicts with "role" and "content".
                For tool results, use role="tool_result" with "tool_use_id" and "content".
            system: Optional system prompt.
            max_tokens: Maximum tokens in the response.
            tools: Optional list of tool definitions in normalized format:
                [{"name": str, "description": str, "input_schema": dict}]

        Returns:
            LLMResponse with text, optional tool_calls, and stop_reason.
        """
        ...

    @property
    def supports_tools(self) -> bool:
        """Whether this provider/model supports tool calling."""
        ...

    @property
    def model_name(self) -> str:
        """The model ID being used."""
        ...

    @property
    def provider_name(self) -> str:
        """Provider identifier (e.g. 'anthropic', 'openai', 'ollama')."""
        ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_provider.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add src/services/llm/__init__.py src/services/llm/provider.py tests/test_llm_provider.py
git commit -m "feat: add LLM provider protocol and data types"
```

---

### Task 3: Implement AnthropicProvider

**Files:**
- Create: `src/services/llm/anthropic_provider.py`
- Test: `tests/test_anthropic_provider.py`

This wraps the existing `anthropic.AsyncAnthropic` client. It translates between our normalized format and Anthropic's native format, including prompt caching via `cache_control`.

**Step 1: Write the failing tests**

```python
# tests/test_anthropic_provider.py
"""Tests for Anthropic LLM provider."""

import json
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from src.services.llm.provider import LLMResponse, ToolCall


class TestAnthropicProvider:
    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.content = [MagicMock(type="text", text="Hello!")]
        client.messages.create = AsyncMock(return_value=response)
        return client

    @pytest.fixture
    def provider(self, mock_client):
        from src.services.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(client=mock_client, model="claude-haiku-4-5-20251001")

    @pytest.mark.asyncio
    async def test_simple_chat(self, provider, mock_client):
        result = await provider.chat(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )
        assert isinstance(result, LLMResponse)
        assert result.text == "Hello!"
        assert result.stop_reason == "end"
        assert result.tool_calls is None

    @pytest.mark.asyncio
    async def test_chat_with_system_prompt(self, provider, mock_client):
        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            system="You are helpful.",
            max_tokens=100,
        )
        call_kwargs = mock_client.messages.create.call_args[1]
        # System should be passed with cache_control
        assert call_kwargs["system"] is not None

    @pytest.mark.asyncio
    async def test_chat_with_tools(self, provider, mock_client):
        # Mock tool use response
        tool_block = MagicMock(type="tool_use", id="t1", name="get_status", input={"name": "plex"})
        response = MagicMock(stop_reason="tool_use", content=[tool_block])
        mock_client.messages.create = AsyncMock(return_value=response)

        tools = [{"name": "get_status", "description": "Get status", "input_schema": {"type": "object", "properties": {}}}]
        result = await provider.chat(
            messages=[{"role": "user", "content": "check plex"}],
            tools=tools,
            max_tokens=100,
        )
        assert result.stop_reason == "tool_use"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_status"
        assert result.tool_calls[0].id == "t1"

    @pytest.mark.asyncio
    async def test_chat_passes_tool_results(self, provider, mock_client):
        """Tool result messages should be translated to Anthropic format."""
        messages = [
            {"role": "user", "content": "check plex"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_status", "input": {}}]},
            {"role": "tool_result", "tool_use_id": "t1", "content": "Running"},
        ]
        await provider.chat(messages=messages, max_tokens=100)
        call_kwargs = mock_client.messages.create.call_args[1]
        # Tool result should be translated to Anthropic's user message with tool_result content
        translated = call_kwargs["messages"]
        assert any(
            isinstance(m.get("content"), list) and
            any(b.get("type") == "tool_result" for b in m["content"] if isinstance(b, dict))
            for m in translated
            if isinstance(m, dict)
        )

    def test_properties(self, provider):
        assert provider.model_name == "claude-haiku-4-5-20251001"
        assert provider.provider_name == "anthropic"
        assert provider.supports_tools is True

    @pytest.mark.asyncio
    async def test_chat_maps_stop_reasons(self, mock_client):
        from src.services.llm.anthropic_provider import AnthropicProvider

        for anthropic_reason, expected in [("end_turn", "end"), ("tool_use", "tool_use"), ("max_tokens", "max_tokens")]:
            response = MagicMock(stop_reason=anthropic_reason, content=[MagicMock(type="text", text="ok")])
            mock_client.messages.create = AsyncMock(return_value=response)
            provider = AnthropicProvider(client=mock_client, model="test")
            result = await provider.chat(messages=[{"role": "user", "content": "hi"}])
            assert result.stop_reason == expected
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_anthropic_provider.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write the implementation**

```python
# src/services/llm/anthropic_provider.py
"""Anthropic LLM provider implementation."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from src.services.llm.provider import LLMProvider, LLMResponse, ToolCall

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger(__name__)

# Map Anthropic stop reasons to our normalized values
_STOP_REASON_MAP = {
    "end_turn": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "end",
}


class AnthropicProvider:
    """LLM provider wrapping Anthropic's AsyncAnthropic client.

    Applies prompt caching (cache_control) on system prompts and tool
    definitions when enable_caching is True.
    """

    def __init__(
        self,
        client: "anthropic.AsyncAnthropic",
        model: str,
        enable_caching: bool = True,
    ):
        self._client = client
        self._model = model
        self._enable_caching = enable_caching

    @property
    def supports_tools(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": self._translate_messages(messages),
        }

        # System prompt with optional caching
        if system:
            if self._enable_caching:
                kwargs["system"] = [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ]
            else:
                kwargs["system"] = system

        # Tools in Anthropic's native format
        if tools:
            native_tools = self._translate_tools(tools)
            kwargs["tools"] = native_tools

        response = await self._client.messages.create(**kwargs)
        return self._translate_response(response)

    def _translate_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate normalized messages to Anthropic format.

        The main translation needed is tool_result messages: our format uses
        role="tool_result" as a flat dict, Anthropic expects role="user" with
        content=[{"type": "tool_result", ...}].
        """
        result = []
        for msg in messages:
            role = msg.get("role", "")

            if role == "tool_result":
                # Translate to Anthropic's format
                result.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg["tool_use_id"],
                        "content": msg["content"],
                    }],
                })
            else:
                # Pass through (assistant messages with content blocks, user messages, etc.)
                result.append(msg)

        return result

    def _translate_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate normalized tool definitions to Anthropic format.

        Our normalized format matches Anthropic's native format (name, description,
        input_schema), so this is mostly a pass-through with optional cache_control.
        """
        native = list(tools)
        if self._enable_caching and native:
            # Add cache_control to last tool for prompt caching
            native[-1] = {**native[-1], "cache_control": {"type": "ephemeral"}}
        return native

    def _translate_response(self, response: Any) -> LLMResponse:
        """Translate Anthropic response to normalized LLMResponse."""
        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        stop_reason = _STOP_REASON_MAP.get(response.stop_reason, "end")

        return LLMResponse(
            text="\n".join(text_parts) if text_parts else "",
            tool_calls=tool_calls if tool_calls else None,
            stop_reason=stop_reason,
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_anthropic_provider.py -v`
Expected: All tests PASS

**Step 5: Run all existing tests to check nothing broke**

Run: `pytest tests/ -x -q`
Expected: All tests pass (this is a new module, no existing code changed)

**Step 6: Commit**

```bash
git add src/services/llm/anthropic_provider.py tests/test_anthropic_provider.py
git commit -m "feat: implement Anthropic LLM provider"
```

---

### Task 4: Implement OpenAIProvider

**Files:**
- Create: `src/services/llm/openai_provider.py`
- Test: `tests/test_openai_provider.py`

Wraps `openai.AsyncOpenAI`. Translates tool definitions from our format (Anthropic-native `input_schema`) to OpenAI's format (wrapped in `{"type": "function", "function": {..., "parameters": ...}}`).

**Step 1: Write the failing tests**

```python
# tests/test_openai_provider.py
"""Tests for OpenAI LLM provider."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock
from src.services.llm.provider import LLMResponse, ToolCall


class TestOpenAIProvider:
    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        # Mock a simple text response
        choice = MagicMock()
        choice.message.content = "Hello!"
        choice.message.tool_calls = None
        choice.finish_reason = "stop"
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create = AsyncMock(return_value=response)
        return client

    @pytest.fixture
    def provider(self, mock_client):
        from src.services.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(client=mock_client, model="gpt-4o")

    @pytest.mark.asyncio
    async def test_simple_chat(self, provider, mock_client):
        result = await provider.chat(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )
        assert isinstance(result, LLMResponse)
        assert result.text == "Hello!"
        assert result.stop_reason == "end"
        assert result.tool_calls is None

    @pytest.mark.asyncio
    async def test_system_prompt_prepended(self, provider, mock_client):
        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            system="You are helpful.",
            max_tokens=100,
        )
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        msgs = call_kwargs["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are helpful."
        assert msgs[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_tools_translated_to_openai_format(self, provider, mock_client):
        tools = [{"name": "get_status", "description": "Get status", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}}]
        await provider.chat(
            messages=[{"role": "user", "content": "check"}],
            tools=tools,
            max_tokens=100,
        )
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        oai_tools = call_kwargs["tools"]
        assert oai_tools[0]["type"] == "function"
        assert oai_tools[0]["function"]["name"] == "get_status"
        assert oai_tools[0]["function"]["parameters"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_tool_use_response(self, provider, mock_client):
        # Mock tool call response
        tool_call = MagicMock()
        tool_call.id = "call_123"
        tool_call.function.name = "get_status"
        tool_call.function.arguments = json.dumps({"name": "plex"})

        choice = MagicMock()
        choice.message.content = None
        choice.message.tool_calls = [tool_call]
        choice.finish_reason = "tool_calls"
        response = MagicMock()
        response.choices = [choice]
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        result = await provider.chat(
            messages=[{"role": "user", "content": "check plex"}],
            max_tokens=100,
        )
        assert result.stop_reason == "tool_use"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_status"
        assert result.tool_calls[0].input == {"name": "plex"}

    @pytest.mark.asyncio
    async def test_tool_results_translated(self, provider, mock_client):
        """Tool result messages should be translated to OpenAI format."""
        messages = [
            {"role": "user", "content": "check plex"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "get_status", "input": {}}]},
            {"role": "tool_result", "tool_use_id": "c1", "content": "Running"},
        ]
        await provider.chat(messages=messages, max_tokens=100)
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        translated = call_kwargs["messages"]
        # Should have role="tool" with tool_call_id
        tool_msgs = [m for m in translated if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "c1"

    def test_properties(self, provider):
        assert provider.model_name == "gpt-4o"
        assert provider.provider_name == "openai"
        assert provider.supports_tools is True

    @pytest.mark.asyncio
    async def test_stop_reason_mapping(self, mock_client):
        from src.services.llm.openai_provider import OpenAIProvider

        for oai_reason, expected in [("stop", "end"), ("tool_calls", "tool_use"), ("length", "max_tokens")]:
            choice = MagicMock()
            choice.message.content = "ok"
            choice.message.tool_calls = None
            choice.finish_reason = oai_reason
            response = MagicMock()
            response.choices = [choice]
            mock_client.chat.completions.create = AsyncMock(return_value=response)
            provider = OpenAIProvider(client=mock_client, model="gpt-4o")
            result = await provider.chat(messages=[{"role": "user", "content": "hi"}])
            assert result.stop_reason == expected
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_openai_provider.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write the implementation**

```python
# src/services/llm/openai_provider.py
"""OpenAI LLM provider implementation."""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from src.services.llm.provider import LLMProvider, LLMResponse, ToolCall

if TYPE_CHECKING:
    import openai

logger = logging.getLogger(__name__)

_STOP_REASON_MAP = {
    "stop": "end",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "end",
}


class OpenAIProvider:
    """LLM provider wrapping OpenAI's AsyncOpenAI client.

    Also used as the base for OllamaProvider since Ollama exposes
    an OpenAI-compatible API.
    """

    def __init__(
        self,
        client: "openai.AsyncOpenAI",
        model: str,
        _supports_tools: bool = True,
    ):
        self._client = client
        self._model = model
        self._supports_tools = _supports_tools

    @property
    def supports_tools(self) -> bool:
        return self._supports_tools

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "openai"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        oai_messages = self._translate_messages(messages, system)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
        }

        if tools and self._supports_tools:
            kwargs["tools"] = self._translate_tools(tools)

        response = await self._client.chat.completions.create(**kwargs)
        return self._translate_response(response)

    def _translate_messages(
        self, messages: list[dict[str, Any]], system: str | None
    ) -> list[dict[str, Any]]:
        """Translate normalized messages to OpenAI format."""
        result = []

        # Prepend system message
        if system:
            result.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role", "")

            if role == "tool_result":
                # Our format: {"role": "tool_result", "tool_use_id": ..., "content": ...}
                # OpenAI format: {"role": "tool", "tool_call_id": ..., "content": ...}
                result.append({
                    "role": "tool",
                    "tool_call_id": msg["tool_use_id"],
                    "content": msg["content"],
                })
            elif role == "assistant" and isinstance(msg.get("content"), list):
                # Anthropic-style assistant message with content blocks
                # Translate to OpenAI format with tool_calls
                text_parts = []
                tool_calls = []
                for block in msg["content"]:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })
                oai_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                }
                if tool_calls:
                    oai_msg["tool_calls"] = tool_calls
                result.append(oai_msg)
            elif role == "assistant" and isinstance(msg.get("tool_calls"), list):
                # Already in a semi-translated format with tool_calls list
                text = msg.get("content", "") or None
                tool_calls = []
                for tc in msg["tool_calls"]:
                    tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("input", {})),
                        },
                    })
                result.append({
                    "role": "assistant",
                    "content": text,
                    "tool_calls": tool_calls,
                })
            else:
                result.append(msg)

        return result

    def _translate_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate normalized tool definitions to OpenAI format.

        Our format: {"name": ..., "description": ..., "input_schema": {...}}
        OpenAI format: {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for tool in tools
        ]

    def _translate_response(self, response: Any) -> LLMResponse:
        """Translate OpenAI response to normalized LLMResponse."""
        choice = response.choices[0]
        message = choice.message

        text = message.content or ""
        tool_calls = None

        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                try:
                    input_data = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    input_data = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=input_data,
                ))

        stop_reason = _STOP_REASON_MAP.get(choice.finish_reason, "end")

        return LLMResponse(
            text=text,
            tool_calls=tool_calls if tool_calls else None,
            stop_reason=stop_reason,
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_openai_provider.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/services/llm/openai_provider.py tests/test_openai_provider.py
git commit -m "feat: implement OpenAI LLM provider"
```

---

### Task 5: Implement OllamaProvider

**Files:**
- Create: `src/services/llm/ollama_provider.py`
- Test: `tests/test_ollama_provider.py`

Extends `OpenAIProvider` with Ollama-specific model discovery via `GET /api/tags`.

**Step 1: Write the failing tests**

```python
# tests/test_ollama_provider.py
"""Tests for Ollama LLM provider."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.services.llm.provider import LLMResponse, ModelInfo


class TestOllamaProvider:
    @pytest.fixture
    def mock_openai_client(self):
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = "Hello from Ollama!"
        choice.message.tool_calls = None
        choice.finish_reason = "stop"
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create = AsyncMock(return_value=response)
        return client

    @pytest.fixture
    def provider(self, mock_openai_client):
        from src.services.llm.ollama_provider import OllamaProvider
        return OllamaProvider(client=mock_openai_client, model="llama3.1")

    @pytest.mark.asyncio
    async def test_simple_chat(self, provider):
        result = await provider.chat(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )
        assert isinstance(result, LLMResponse)
        assert result.text == "Hello from Ollama!"

    def test_provider_name(self, provider):
        assert provider.provider_name == "ollama"

    def test_model_name(self, provider):
        assert provider.model_name == "llama3.1"

    @pytest.mark.asyncio
    async def test_discover_models(self):
        """Test model discovery from Ollama API."""
        from src.services.llm.ollama_provider import OllamaProvider

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "models": [
                {"name": "llama3.1:latest", "details": {"family": "llama"}},
                {"name": "mistral:latest", "details": {"family": "mistral"}},
            ]
        })

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False),
        ))

        models = await OllamaProvider.discover_models(
            host="http://localhost:11434",
            session=mock_session,
        )
        assert len(models) == 2
        assert models[0].id == "llama3.1:latest"
        assert models[0].provider == "ollama"

    @pytest.mark.asyncio
    async def test_discover_models_handles_connection_error(self):
        """Test graceful handling when Ollama is not running."""
        from src.services.llm.ollama_provider import OllamaProvider
        import aiohttp

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))

        models = await OllamaProvider.discover_models(
            host="http://localhost:11434",
            session=mock_session,
        )
        assert models == []
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_ollama_provider.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write the implementation**

```python
# src/services/llm/ollama_provider.py
"""Ollama LLM provider implementation.

Uses the OpenAI-compatible API that Ollama exposes at /v1/.
Model discovery uses Ollama's native /api/tags endpoint.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from src.services.llm.openai_provider import OpenAIProvider
from src.services.llm.provider import ModelInfo

if TYPE_CHECKING:
    import aiohttp
    import openai

logger = logging.getLogger(__name__)

# Models known to support tool calling via Ollama
_TOOL_CAPABLE_FAMILIES = {"llama", "mistral", "qwen", "command-r", "gemma"}


class OllamaProvider(OpenAIProvider):
    """LLM provider for Ollama using its OpenAI-compatible API."""

    def __init__(
        self,
        client: "openai.AsyncOpenAI",
        model: str,
        supports_tools: bool = False,
    ):
        super().__init__(client=client, model=model, _supports_tools=supports_tools)

    @property
    def provider_name(self) -> str:
        return "ollama"

    @staticmethod
    async def discover_models(
        host: str = "http://localhost:11434",
        session: "aiohttp.ClientSession | None" = None,
    ) -> list[ModelInfo]:
        """Discover available models from Ollama's /api/tags endpoint.

        Args:
            host: Ollama server URL.
            session: Optional aiohttp session (creates one if not provided).

        Returns:
            List of ModelInfo for available models.
        """
        import aiohttp

        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()

        try:
            async with session.get(f"{host}/api/tags") as resp:
                if resp.status != 200:
                    logger.warning(f"Ollama returned status {resp.status}")
                    return []
                data = await resp.json()

            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                family = m.get("details", {}).get("family", "").lower()
                supports_tools = any(f in family for f in _TOOL_CAPABLE_FAMILIES)
                models.append(ModelInfo(
                    id=name,
                    name=name.split(":")[0].title(),
                    provider="ollama",
                    supports_tools=supports_tools,
                ))
            return models
        except Exception as e:
            logger.warning(f"Failed to discover Ollama models: {e}")
            return []
        finally:
            if own_session:
                await session.close()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_ollama_provider.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/services/llm/ollama_provider.py tests/test_ollama_provider.py
git commit -m "feat: implement Ollama LLM provider with model discovery"
```

---

### Task 6: Implement ProviderRegistry

**Files:**
- Create: `src/services/llm/registry.py`
- Test: `tests/test_provider_registry.py`
- Modify: `src/services/llm/__init__.py` (add ProviderRegistry export)

The registry creates providers based on available API keys, manages the active model selection, and resolves per-feature overrides.

**Step 1: Write the failing tests**

```python
# tests/test_provider_registry.py
"""Tests for LLM provider registry."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.services.llm.provider import LLMResponse


class TestProviderRegistry:
    @pytest.fixture
    def registry_with_anthropic(self):
        from src.services.llm.registry import ProviderRegistry

        mock_anthropic_client = MagicMock()
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.content = [MagicMock(type="text", text="Hi")]
        mock_anthropic_client.messages.create = AsyncMock(return_value=response)

        return ProviderRegistry(
            anthropic_client=mock_anthropic_client,
            default_model="claude-haiku-4-5-20251001",
        )

    def test_get_provider_returns_provider(self, registry_with_anthropic):
        provider = registry_with_anthropic.get_provider()
        assert provider is not None
        assert provider.provider_name == "anthropic"

    def test_get_provider_with_feature_override(self):
        from src.services.llm.registry import ProviderRegistry

        mock_client = MagicMock()
        response = MagicMock(stop_reason="end_turn", content=[MagicMock(type="text", text="Hi")])
        mock_client.messages.create = AsyncMock(return_value=response)

        registry = ProviderRegistry(
            anthropic_client=mock_client,
            default_model="claude-haiku-4-5-20251001",
            feature_models={"nl_processor": "claude-sonnet-4-5-20250929"},
        )
        default = registry.get_provider()
        nl = registry.get_provider("nl_processor")
        assert default.model_name == "claude-haiku-4-5-20251001"
        assert nl.model_name == "claude-sonnet-4-5-20250929"

    def test_get_available_providers_lists_configured(self, registry_with_anthropic):
        providers = registry_with_anthropic.get_available_providers()
        names = [p.name for p in providers]
        assert "anthropic" in names

    def test_set_model_changes_default(self, registry_with_anthropic):
        registry_with_anthropic.set_model("anthropic", "claude-sonnet-4-5-20250929")
        provider = registry_with_anthropic.get_provider()
        assert provider.model_name == "claude-sonnet-4-5-20250929"

    def test_get_provider_returns_none_when_no_providers(self):
        from src.services.llm.registry import ProviderRegistry
        registry = ProviderRegistry()
        assert registry.get_provider() is None

    def test_resolve_provider_from_model_name(self):
        from src.services.llm.registry import ProviderRegistry

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock()

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock()

        registry = ProviderRegistry(
            anthropic_client=mock_anthropic,
            openai_client=mock_openai,
            default_model="claude-haiku-4-5-20251001",
        )
        # Switch to an OpenAI model
        registry.set_model("openai", "gpt-4o")
        provider = registry.get_provider()
        assert provider.provider_name == "openai"
        assert provider.model_name == "gpt-4o"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_provider_registry.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write the implementation**

```python
# src/services/llm/registry.py
"""Provider registry for managing LLM providers and model selection."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src.services.llm.provider import LLMProvider, ModelInfo

if TYPE_CHECKING:
    import anthropic
    import openai

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    """Information about a registered provider."""

    name: str
    display_name: str
    available_models: list[ModelInfo]


# Well-known models per provider
ANTHROPIC_MODELS = [
    ModelInfo(id="claude-sonnet-4-5-20250929", name="Claude Sonnet 4.5", provider="anthropic", supports_tools=True),
    ModelInfo(id="claude-haiku-4-5-20251001", name="Claude Haiku 4.5", provider="anthropic", supports_tools=True),
]

OPENAI_MODELS = [
    ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai", supports_tools=True),
    ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini", provider="openai", supports_tools=True),
    ModelInfo(id="gpt-4.1", name="GPT-4.1", provider="openai", supports_tools=True),
    ModelInfo(id="gpt-4.1-mini", name="GPT-4.1 Mini", provider="openai", supports_tools=True),
    ModelInfo(id="gpt-4.1-nano", name="GPT-4.1 Nano", provider="openai", supports_tools=True),
]


class ProviderRegistry:
    """Manages LLM providers and active model selection.

    Creates providers based on available API keys/clients. Supports
    a global default model and per-feature overrides.
    """

    def __init__(
        self,
        anthropic_client: "anthropic.AsyncAnthropic | None" = None,
        openai_client: "openai.AsyncOpenAI | None" = None,
        ollama_client: "openai.AsyncOpenAI | None" = None,
        ollama_models: list[ModelInfo] | None = None,
        default_model: str | None = None,
        feature_models: dict[str, str] | None = None,
        persistence_path: str = "data/model_selection.json",
    ):
        self._anthropic_client = anthropic_client
        self._openai_client = openai_client
        self._ollama_client = ollama_client
        self._ollama_models = ollama_models or []
        self._feature_models = feature_models or {}
        self._persistence_path = Path(persistence_path)

        # Determine default model: explicit > persisted > first available
        self._default_provider: str | None = None
        self._default_model: str | None = None

        # Try loading persisted selection
        persisted = self._load_persisted()
        if persisted:
            self._default_provider = persisted.get("provider")
            self._default_model = persisted.get("model")

        # Override with explicit default if provided
        if default_model:
            self._default_model = default_model
            self._default_provider = self._detect_provider(default_model)

        # Fallback: pick first available provider
        if not self._default_model:
            if anthropic_client:
                self._default_model = "claude-haiku-4-5-20251001"
                self._default_provider = "anthropic"
            elif openai_client:
                self._default_model = "gpt-4o-mini"
                self._default_provider = "openai"
            elif ollama_client and self._ollama_models:
                self._default_model = self._ollama_models[0].id
                self._default_provider = "ollama"

    def get_provider(self, feature: str = "default") -> LLMProvider | None:
        """Get the LLM provider for a feature.

        Checks per-feature overrides first, then falls back to default.
        Returns None if no provider is configured.
        """
        # Determine model to use
        model = self._feature_models.get(feature, self._default_model)
        if not model:
            return None

        provider_name = self._detect_provider(model)
        return self._create_provider(provider_name, model)

    def set_model(self, provider_name: str, model_name: str) -> None:
        """Switch the global default model.

        Args:
            provider_name: Provider key ("anthropic", "openai", "ollama").
            model_name: Model ID to use.
        """
        self._default_provider = provider_name
        self._default_model = model_name
        self._persist_selection()

    def get_available_providers(self) -> list[ProviderInfo]:
        """List all configured providers with their available models."""
        providers = []

        if self._anthropic_client:
            providers.append(ProviderInfo(
                name="anthropic",
                display_name="Anthropic",
                available_models=list(ANTHROPIC_MODELS),
            ))

        if self._openai_client:
            providers.append(ProviderInfo(
                name="openai",
                display_name="OpenAI",
                available_models=list(OPENAI_MODELS),
            ))

        if self._ollama_client:
            providers.append(ProviderInfo(
                name="ollama",
                display_name="Ollama",
                available_models=list(self._ollama_models),
            ))

        return providers

    def get_current_model(self) -> tuple[str, str] | None:
        """Return (provider_name, model_name) for the current default, or None."""
        if self._default_provider and self._default_model:
            return (self._default_provider, self._default_model)
        return None

    def _detect_provider(self, model: str) -> str:
        """Detect provider from model name."""
        if model.startswith("claude-"):
            return "anthropic"
        if model.startswith("gpt-") or model.startswith("o1") or model.startswith("o3") or model.startswith("o4"):
            return "openai"
        # Check if it's an Ollama model
        ollama_ids = {m.id for m in self._ollama_models}
        if model in ollama_ids:
            return "ollama"
        # Default to ollama for unknown models (user may have custom models)
        if self._ollama_client:
            return "ollama"
        return "anthropic"

    def _create_provider(self, provider_name: str, model: str) -> LLMProvider | None:
        """Create a provider instance for the given provider and model."""
        if provider_name == "anthropic" and self._anthropic_client:
            from src.services.llm.anthropic_provider import AnthropicProvider
            return AnthropicProvider(client=self._anthropic_client, model=model)

        if provider_name == "openai" and self._openai_client:
            from src.services.llm.openai_provider import OpenAIProvider
            return OpenAIProvider(client=self._openai_client, model=model)

        if provider_name == "ollama" and self._ollama_client:
            from src.services.llm.ollama_provider import OllamaProvider
            # Check tool support for this specific model
            model_info = next((m for m in self._ollama_models if m.id == model), None)
            supports_tools = model_info.supports_tools if model_info else False
            return OllamaProvider(
                client=self._ollama_client,
                model=model,
                supports_tools=supports_tools,
            )

        return None

    def _persist_selection(self) -> None:
        """Save current model selection to disk."""
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"provider": self._default_provider, "model": self._default_model}
            self._persistence_path.write_text(json.dumps(data))
        except Exception as e:
            logger.warning(f"Failed to persist model selection: {e}")

    def _load_persisted(self) -> dict | None:
        """Load persisted model selection."""
        try:
            if self._persistence_path.exists():
                return json.loads(self._persistence_path.read_text())
        except Exception as e:
            logger.warning(f"Failed to load persisted model selection: {e}")
        return None
```

Also update `__init__.py`:

```python
# src/services/llm/__init__.py
"""Multi-provider LLM abstraction layer."""

from src.services.llm.provider import LLMProvider, LLMResponse, ToolCall, ModelInfo
from src.services.llm.registry import ProviderRegistry

__all__ = ["LLMProvider", "LLMResponse", "ToolCall", "ModelInfo", "ProviderRegistry"]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_provider_registry.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/services/llm/registry.py src/services/llm/__init__.py tests/test_provider_registry.py
git commit -m "feat: implement provider registry with model selection"
```

---

### Task 7: Update configuration for multi-provider support

**Files:**
- Modify: `src/config.py:57-99` (AIConfig)
- Modify: `src/config.py:264-296` (Settings)
- Modify: `src/config.py:298-393` (AppConfig)
- Test: `tests/test_config.py` (add new tests)

**Step 1: Write the failing tests**

Add to a new test file (or append to existing config tests):

```python
# tests/test_multi_provider_config.py
"""Tests for multi-provider configuration."""

import os
import pytest
from unittest.mock import patch


class TestMultiProviderSettings:
    def test_openai_api_key_optional(self):
        from src.config import Settings
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "test",
            "TELEGRAM_ALLOWED_USERS": "123",
        }, clear=True):
            settings = Settings()
            assert settings.openai_api_key is None

    def test_openai_api_key_loaded(self):
        from src.config import Settings
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "test",
            "TELEGRAM_ALLOWED_USERS": "123",
            "OPENAI_API_KEY": "sk-test",
        }, clear=True):
            settings = Settings()
            assert settings.openai_api_key == "sk-test"

    def test_ollama_host_default(self):
        from src.config import Settings
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "test",
            "TELEGRAM_ALLOWED_USERS": "123",
        }, clear=True):
            settings = Settings()
            assert settings.ollama_host is None


class TestAIConfigMultiProvider:
    def test_default_provider_from_yaml(self):
        from src.config import AIConfig
        config = AIConfig.from_dict({
            "default_provider": "openai",
            "default_model": "gpt-4o",
        })
        assert config.default_provider == "openai"
        assert config.default_model == "gpt-4o"

    def test_default_provider_fallback(self):
        from src.config import AIConfig
        config = AIConfig.from_dict({})
        assert config.default_provider == "anthropic"
        assert config.default_model == "claude-haiku-4-5-20251001"

    def test_provider_settings(self):
        from src.config import AIConfig
        config = AIConfig.from_dict({
            "providers": {
                "anthropic": {"prompt_caching": False},
                "ollama": {"host": "http://my-server:11434"},
            }
        })
        assert config.anthropic_prompt_caching is False
        assert config.ollama_host == "http://my-server:11434"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_multi_provider_config.py -v`
Expected: FAIL — Settings missing `openai_api_key`, AIConfig missing new fields

**Step 3: Update Settings and AIConfig**

In `src/config.py`, add to `Settings`:
```python
openai_api_key: str | None = None
ollama_host: str | None = None
```

In `AIConfig`, add new fields and update `from_dict`:
```python
# New fields
default_provider: str = "anthropic"
default_model: str = "claude-haiku-4-5-20251001"
anthropic_prompt_caching: bool = True
ollama_host: str = "http://localhost:11434"
```

And update `from_dict` to parse the new `providers` section and `default_provider`/`default_model`.

In `AppConfig`, add properties:
```python
@property
def openai_api_key(self) -> str | None:
    return self._settings.openai_api_key

@property
def ollama_host(self) -> str | None:
    return self._settings.ollama_host
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_multi_provider_config.py -v`
Expected: All tests PASS

**Step 5: Run all existing config tests**

Run: `pytest tests/test_config.py tests/test_config_extended.py tests/test_default_config.py tests/test_config_protected.py -v`
Expected: All existing tests still pass

**Step 6: Commit**

```bash
git add src/config.py tests/test_multi_provider_config.py
git commit -m "feat: add multi-provider config (OpenAI key, Ollama host, default provider)"
```

---

### Task 8: Update api_errors.py for multi-provider error handling

**Files:**
- Modify: `src/utils/api_errors.py`
- Modify: `tests/test_api_errors.py`

The current `handle_anthropic_error` is Anthropic-specific. Rename to `handle_llm_error` and add OpenAI error handling.

**Step 1: Write the failing test**

```python
# Add to tests/test_api_errors.py

def test_handle_openai_rate_limit():
    from src.utils.api_errors import handle_llm_error
    import openai
    error = openai.RateLimitError("rate limited", response=MagicMock(), body=None)
    result = handle_llm_error(error)
    assert result.is_retryable is True
    assert "rate limit" in result.user_message.lower()


def test_handle_openai_auth_error():
    from src.utils.api_errors import handle_llm_error
    import openai
    error = openai.AuthenticationError("bad key", response=MagicMock(), body=None)
    result = handle_llm_error(error)
    assert result.is_retryable is False
    assert "authentication" in result.user_message.lower()


def test_handle_llm_error_backward_compat():
    """handle_anthropic_error still works as alias."""
    from src.utils.api_errors import handle_anthropic_error
    result = handle_anthropic_error(RuntimeError("test"))
    assert result.user_message is not None
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_errors.py -v`
Expected: FAIL — `handle_llm_error` not found, no OpenAI error handling

**Step 3: Update api_errors.py**

Rename the main function to `handle_llm_error`, add OpenAI error handling, keep `handle_anthropic_error` as an alias. All existing callers use `handle_anthropic_error` — they continue to work unchanged.

Add OpenAI error handling block:
```python
try:
    import openai
    if isinstance(error, openai.RateLimitError):
        return APIErrorResult(user_message="...", is_retryable=True, log_level=logging.WARNING)
    if isinstance(error, openai.AuthenticationError):
        return APIErrorResult(user_message="...", is_retryable=False)
    # etc.
except ImportError:
    pass
```

**Step 4: Run tests**

Run: `pytest tests/test_api_errors.py -v`
Expected: All tests PASS (old and new)

**Step 5: Commit**

```bash
git add src/utils/api_errors.py tests/test_api_errors.py
git commit -m "feat: add OpenAI error handling to api_errors, rename to handle_llm_error"
```

---

### Task 9: Migrate AI consumers to use LLMProvider

**Files:**
- Modify: `src/services/nl_processor.py`
- Modify: `src/services/diagnostic.py`
- Modify: `src/analysis/pattern_analyzer.py`
- Modify: `src/services/container_classifier.py`
- Modify: existing tests for these modules

This is the biggest task. Each consumer switches from `anthropic_client` to `LLMProvider`.

**Step 1: Migrate PatternAnalyzer (simplest consumer)**

In `src/analysis/pattern_analyzer.py`:
- Change constructor: `anthropic_client` → `provider: LLMProvider | None`
- Replace `self._client.messages.create(model=..., max_tokens=..., messages=[...])` with `self._provider.chat(messages=[...], max_tokens=...)`
- Replace `response.content[0].text` with `response.text`
- Remove `self._model` and `self._max_tokens` fields (they're now provider properties / passed as args)

Update `tests/test_pattern_analyzer.py`:
- Change `mock_anthropic_client` fixture to return a mock `LLMProvider` (returns `LLMResponse` from `chat()`)

**Step 2: Run pattern analyzer tests**

Run: `pytest tests/test_pattern_analyzer.py -v`
Expected: All tests PASS

**Step 3: Migrate ContainerClassifier**

In `src/services/container_classifier.py`:
- Change constructor: `anthropic_client` → `provider: LLMProvider | None`
- Replace API call with `self._provider.chat(messages=[...], max_tokens=1024)`
- Replace `response.content[0].text` with `response.text`

Update `tests/test_container_classifier.py` similarly.

**Step 4: Run classifier tests**

Run: `pytest tests/test_container_classifier.py -v`
Expected: All tests PASS

**Step 5: Migrate DiagnosticService**

In `src/services/diagnostic.py`:
- Change constructor: `anthropic_client` → `provider: LLMProvider | None`
- Replace both `self._anthropic.messages.create(...)` calls with `self._provider.chat(...)`
- Remove `cache_control` from message content (provider handles caching transparently)
- Replace `message.content[0].text` with `response.text`
- Keep `self._brief_max_tokens` and `self._detail_max_tokens` — passed to `provider.chat()`

Update `tests/test_diagnostic.py` and `tests/test_diagnose_integration.py`.

**Step 6: Run diagnostic tests**

Run: `pytest tests/test_diagnostic.py tests/test_diagnose_integration.py tests/test_diagnose_command.py -v`
Expected: All tests PASS

**Step 7: Migrate NLProcessor (most complex)**

In `src/services/nl_processor.py`:
- Change constructor: `anthropic_client` → `provider: LLMProvider | None`
- Remove `self._model` (provider has it)
- Replace `_call_claude` with `_call_llm` that uses `self._provider.chat()`
- The tool-use loop changes from iterating `response.content` blocks to using `response.tool_calls`
- Tool results sent back as `{"role": "tool_result", "tool_use_id": ..., "content": ...}`
- For non-tool-capable providers: skip tool definitions, return text-only with note
- Remove `cache_control` handling (provider does it)
- Keep `_cached_tools` (still cache tool definitions for efficiency, but they're now the normalized format from `get_tool_definitions()`)

Key change to the tool loop:
```python
# Before:
for block in response.content:
    if block.type == "tool_use":
        result = await self._executor.execute(block.name, block.input)
        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

# After:
for tc in response.tool_calls:
    result = await self._executor.execute(tc.name, tc.input)
    tool_results.append({"role": "tool_result", "tool_use_id": tc.id, "content": result})
```

Update `tests/test_nl_processor.py` and `tests/test_nl_integration.py`.

**Step 8: Run all NL tests**

Run: `pytest tests/test_nl_processor.py tests/test_nl_handler.py tests/test_nl_integration.py tests/test_nl_tools.py -v`
Expected: All tests PASS

**Step 9: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass

**Step 10: Commit**

```bash
git add src/services/nl_processor.py src/services/diagnostic.py src/analysis/pattern_analyzer.py src/services/container_classifier.py tests/
git commit -m "refactor: migrate all AI consumers to LLMProvider abstraction"
```

---

### Task 10: Wire ProviderRegistry into main.py

**Files:**
- Modify: `src/main.py:162-511` (start_monitoring function)
- Modify: `src/bot/telegram_bot.py:230-504` (register_commands)

**Step 1: Update start_monitoring**

In `src/main.py`, replace the Anthropic client setup (lines ~184-196) with ProviderRegistry creation:

```python
# Before:
anthropic_client = None
pattern_analyzer = None
if config.anthropic_api_key:
    anthropic_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
    pattern_analyzer = PatternAnalyzer(anthropic_client, ...)

# After:
from src.services.llm.registry import ProviderRegistry
from src.services.llm.ollama_provider import OllamaProvider
import openai as openai_sdk

# Build provider registry from available keys
anthropic_client = None
openai_client = None
ollama_client = None
ollama_models = []

if config.anthropic_api_key:
    anthropic_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

if settings.openai_api_key:
    openai_client = openai_sdk.AsyncOpenAI(api_key=settings.openai_api_key)

if settings.ollama_host:
    ollama_client = openai_sdk.AsyncOpenAI(
        base_url=f"{settings.ollama_host}/v1",
        api_key="ollama",  # Ollama doesn't need a real key
    )
    # Discover models (best-effort, non-blocking)
    try:
        ollama_models = await OllamaProvider.discover_models(host=settings.ollama_host)
    except Exception as e:
        logger.warning(f"Failed to discover Ollama models: {e}")

registry = ProviderRegistry(
    anthropic_client=anthropic_client,
    openai_client=openai_client,
    ollama_client=ollama_client,
    ollama_models=ollama_models,
    default_model=ai_config.default_model,
    feature_models={
        "nl_processor": ai_config.nl_processor_model,
        "diagnostic": ai_config.diagnostic_model,
        "pattern_analyzer": ai_config.pattern_analyzer_model,
    },
)

# Create pattern analyzer using registry
pattern_analyzer_provider = registry.get_provider("pattern_analyzer")
pattern_analyzer = PatternAnalyzer(
    provider=pattern_analyzer_provider,
    max_tokens=ai_config.pattern_analyzer_max_tokens,
    context_lines=ai_config.pattern_analyzer_context_lines,
) if pattern_analyzer_provider else None
```

Similarly update NLProcessor, DiagnosticService, and ContainerClassifier creation to use `registry.get_provider(feature)`.

Pass `registry` to `register_commands` instead of `anthropic_client`.

**Step 2: Update register_commands signature**

In `src/bot/telegram_bot.py`, replace `anthropic_client: Any | None = None` with `registry: Any | None = None` (or proper type). Update DiagnosticService creation to use `registry.get_provider("diagnostic")`.

**Step 3: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass

**Step 4: Commit**

```bash
git add src/main.py src/bot/telegram_bot.py
git commit -m "feat: wire ProviderRegistry into main.py and register_commands"
```

---

### Task 11: Implement /model command

**Files:**
- Create: `src/bot/model_command.py`
- Test: `tests/test_model_command.py`
- Modify: `src/bot/telegram_bot.py` (register the command)

**Step 1: Write the failing tests**

```python
# tests/test_model_command.py
"""Tests for /model command."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from src.services.llm.provider import ModelInfo
from src.services.llm.registry import ProviderInfo


class TestModelCommand:
    @pytest.fixture
    def mock_registry(self):
        registry = MagicMock()
        registry.get_current_model.return_value = ("anthropic", "claude-haiku-4-5-20251001")
        registry.get_available_providers.return_value = [
            ProviderInfo(
                name="anthropic",
                display_name="Anthropic",
                available_models=[
                    ModelInfo(id="claude-haiku-4-5-20251001", name="Claude Haiku", provider="anthropic"),
                    ModelInfo(id="claude-sonnet-4-5-20250929", name="Claude Sonnet", provider="anthropic"),
                ],
            ),
            ProviderInfo(
                name="openai",
                display_name="OpenAI",
                available_models=[
                    ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai"),
                ],
            ),
        ]
        return registry

    @pytest.mark.asyncio
    async def test_model_command_shows_current_and_providers(self, mock_registry):
        from src.bot.model_command import model_command

        message = MagicMock()
        message.answer = AsyncMock()

        handler = model_command(mock_registry)
        await handler(message)

        message.answer.assert_called_once()
        call_text = message.answer.call_args[0][0]
        assert "claude-haiku" in call_text.lower() or "Claude Haiku" in call_text

    @pytest.mark.asyncio
    async def test_model_provider_callback_shows_models(self, mock_registry):
        from src.bot.model_command import model_provider_callback

        callback = MagicMock()
        callback.data = "model:anthropic"
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        handler = model_provider_callback(mock_registry)
        await handler(callback)

        callback.message.edit_text.assert_called_once()
        call_text = callback.message.edit_text.call_args[0][0]
        assert "Claude Haiku" in call_text or "claude" in call_text.lower()

    @pytest.mark.asyncio
    async def test_model_select_callback_switches_model(self, mock_registry):
        from src.bot.model_command import model_select_callback

        callback = MagicMock()
        callback.data = "model_select:openai:gpt-4o"
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        handler = model_select_callback(mock_registry)
        await handler(callback)

        mock_registry.set_model.assert_called_once_with("openai", "gpt-4o")
        callback.message.edit_text.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_model_command.py -v`
Expected: FAIL — module not found

**Step 3: Write the implementation**

```python
# src/bot/model_command.py
"""Handler for /model command — runtime LLM model switching."""

import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

if TYPE_CHECKING:
    from src.services.llm.registry import ProviderRegistry

logger = logging.getLogger(__name__)


def model_command(registry: "ProviderRegistry") -> Callable[[Message], Awaitable[None]]:
    """Factory for /model command handler."""

    async def handler(message: Message) -> None:
        current = registry.get_current_model()
        providers = registry.get_available_providers()

        if not providers:
            await message.answer("No AI providers configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OLLAMA_HOST.")
            return

        if current:
            provider_name, model_name = current
            text = f"Current model: *{model_name}* ({provider_name})\n\nSelect a provider:"
        else:
            text = "No model selected.\n\nSelect a provider:"

        # Build provider buttons
        buttons = []
        for p in providers:
            label = f"{p.display_name} ({len(p.available_models)} models)"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model:{p.name}")])

        try:
            await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")
        except TelegramBadRequest:
            await message.answer(text.replace("*", ""), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    return handler


def model_provider_callback(registry: "ProviderRegistry") -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for model provider selection callback."""

    async def handler(callback: CallbackQuery) -> None:
        provider_name = callback.data.split(":", 1)[1]
        providers = registry.get_available_providers()
        provider = next((p for p in providers if p.name == provider_name), None)

        if not provider:
            await callback.answer("Provider not found")
            return

        current = registry.get_current_model()
        current_model = current[1] if current else None

        text = f"{provider.display_name} models:"
        buttons = []
        for m in provider.available_models:
            label = m.name
            if m.id == current_model:
                label = f"✓ {label}"
            if not m.supports_tools:
                label = f"{label} (no tools)"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_select:{provider_name}:{m.id}")])

        # Add back button
        buttons.append([InlineKeyboardButton(text="← Back", callback_data="model:back")])

        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await callback.answer()

    return handler


def model_select_callback(registry: "ProviderRegistry") -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for model selection callback."""

    async def handler(callback: CallbackQuery) -> None:
        parts = callback.data.split(":", 2)
        if len(parts) != 3:
            await callback.answer("Invalid selection")
            return

        _, provider_name, model_name = parts
        registry.set_model(provider_name, model_name)

        await callback.message.edit_text(f"Switched to *{model_name}* ({provider_name})", parse_mode="Markdown")
        await callback.answer("Model switched!")

    return handler


def model_back_callback(registry: "ProviderRegistry") -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for back button callback — re-shows provider list."""

    async def handler(callback: CallbackQuery) -> None:
        current = registry.get_current_model()
        providers = registry.get_available_providers()

        if current:
            provider_name, model_name = current
            text = f"Current model: *{model_name}* ({provider_name})\n\nSelect a provider:"
        else:
            text = "No model selected.\n\nSelect a provider:"

        buttons = []
        for p in providers:
            label = f"{p.display_name} ({len(p.available_models)} models)"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model:{p.name}")])

        try:
            await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")
        except TelegramBadRequest:
            await callback.message.edit_text(text.replace("*", ""), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await callback.answer()

    return handler
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_model_command.py -v`
Expected: All tests PASS

**Step 5: Register the command in telegram_bot.py**

In `register_commands()`, add after the NL handler registration:

```python
# Register /model command
if registry is not None:
    from src.bot.model_command import model_command, model_provider_callback, model_select_callback, model_back_callback

    dp.message.register(model_command(registry), Command("model"))
    dp.callback_query.register(model_provider_callback(registry), F.data.startswith("model:") & ~F.data.startswith("model_select:") & (F.data != "model:back"))
    dp.callback_query.register(model_back_callback(registry), F.data == "model:back")
    dp.callback_query.register(model_select_callback(registry), F.data.startswith("model_select:"))
```

Also add `/model` to the help text in `src/bot/commands.py`.

**Step 6: Run all tests**

Run: `pytest tests/ -x -q`
Expected: All tests pass

**Step 7: Commit**

```bash
git add src/bot/model_command.py src/bot/telegram_bot.py src/bot/commands.py tests/test_model_command.py
git commit -m "feat: add /model command for runtime LLM provider switching"
```

---

### Task 12: Update CLAUDE.md, structure YAML, and docs

**Files:**
- Modify: `CLAUDE.md` (update file map, env vars, architecture)
- Modify: `.claude/structure/bot.yaml` (add model_command)
- Create or update: `.claude/structure/llm.yaml` (new module)

**Step 1: Update CLAUDE.md**

Add to the File Map:
```
# LLM Providers
src/services/llm/__init__.py - Re-exports LLMProvider, ProviderRegistry
src/services/llm/provider.py - LLMProvider protocol, LLMResponse, ToolCall, ModelInfo
src/services/llm/registry.py - ProviderRegistry for managing providers and model selection
src/services/llm/anthropic_provider.py - Anthropic provider with prompt caching
src/services/llm/openai_provider.py - OpenAI provider with function calling translation
src/services/llm/ollama_provider.py - Ollama provider with model discovery
```

Add to Bot section: `src/bot/model_command.py - /model command for runtime LLM switching`

Update Environment Variables section with `OPENAI_API_KEY` and `OLLAMA_HOST`.

Update Architecture section to mention multi-provider support.

**Step 2: Create structure YAML for LLM module**

**Step 3: Commit**

```bash
git add CLAUDE.md .claude/structure/
git commit -m "docs: update CLAUDE.md and structure files for multi-provider LLM"
```

---

### Task 13: Final integration test and cleanup

**Files:**
- Test: `tests/test_multi_provider_integration.py`

**Step 1: Write integration test**

```python
# tests/test_multi_provider_integration.py
"""Integration tests for multi-provider LLM support."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from src.services.llm.provider import LLMResponse, ToolCall
from src.services.llm.registry import ProviderRegistry


class TestMultiProviderIntegration:
    @pytest.mark.asyncio
    async def test_anthropic_provider_through_registry(self):
        """Full flow: registry -> anthropic provider -> NL-style response."""
        mock_client = MagicMock()
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.content = [MagicMock(type="text", text="Plex is running fine.")]
        mock_client.messages.create = AsyncMock(return_value=response)

        registry = ProviderRegistry(
            anthropic_client=mock_client,
            default_model="claude-haiku-4-5-20251001",
        )
        provider = registry.get_provider()
        assert provider is not None

        result = await provider.chat(
            messages=[{"role": "user", "content": "how is plex?"}],
            system="You are a server monitor assistant.",
        )
        assert "Plex" in result.text

    @pytest.mark.asyncio
    async def test_switch_provider_at_runtime(self):
        """Switch from Anthropic to OpenAI at runtime."""
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=MagicMock(stop_reason="end_turn", content=[MagicMock(type="text", text="anthropic")])
        )
        mock_openai = MagicMock()
        choice = MagicMock()
        choice.message.content = "openai"
        choice.message.tool_calls = None
        choice.finish_reason = "stop"
        mock_openai.chat.completions.create = AsyncMock(return_value=MagicMock(choices=[choice]))

        registry = ProviderRegistry(
            anthropic_client=mock_anthropic,
            openai_client=mock_openai,
            default_model="claude-haiku-4-5-20251001",
        )

        # Initially Anthropic
        p1 = registry.get_provider()
        assert p1.provider_name == "anthropic"

        # Switch to OpenAI
        registry.set_model("openai", "gpt-4o")
        p2 = registry.get_provider()
        assert p2.provider_name == "openai"

        result = await p2.chat(messages=[{"role": "user", "content": "hi"}])
        assert result.text == "openai"

    @pytest.mark.asyncio
    async def test_graceful_degradation_no_tools(self):
        """Provider without tool support returns text-only response."""
        from src.services.llm.openai_provider import OpenAIProvider

        mock_client = MagicMock()
        choice = MagicMock()
        choice.message.content = "I can't use tools, but here's info."
        choice.message.tool_calls = None
        choice.finish_reason = "stop"
        mock_client.chat.completions.create = AsyncMock(return_value=MagicMock(choices=[choice]))

        provider = OpenAIProvider(client=mock_client, model="test", _supports_tools=False)

        tools = [{"name": "get_status", "description": "test", "input_schema": {"type": "object"}}]
        result = await provider.chat(
            messages=[{"role": "user", "content": "check plex"}],
            tools=tools,
        )
        # Tools should NOT be passed to the API call
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "tools" not in call_kwargs
        assert result.text == "I can't use tools, but here's info."
```

**Step 2: Run integration test**

Run: `pytest tests/test_multi_provider_integration.py -v`
Expected: All tests PASS

**Step 3: Run full test suite**

Run: `pytest tests/ --tb=short -q`
Expected: All tests pass, no regressions

**Step 4: Run type checker**

Run: `mypy src/services/llm/`
Expected: No errors (or only pre-existing ones)

**Step 5: Run linter**

Run: `ruff check src/services/llm/ src/bot/model_command.py`
Expected: No errors

**Step 6: Commit**

```bash
git add tests/test_multi_provider_integration.py
git commit -m "test: add multi-provider integration tests"
```
