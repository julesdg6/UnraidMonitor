# tests/test_anthropic_provider.py
"""Tests for Anthropic LLM provider."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services.llm.anthropic_provider import AnthropicProvider
from src.services.llm.provider import LLMProvider, LLMResponse, ToolCall


# -- Helpers to build mock Anthropic responses --

def _make_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(tool_id: str, name: str, input_data: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_data
    return block


def _make_response(content_blocks: list, stop_reason: str = "end_turn") -> MagicMock:
    resp = MagicMock()
    resp.content = content_blocks
    resp.stop_reason = stop_reason
    return resp


def _make_client(response: MagicMock) -> MagicMock:
    """Build a mock anthropic.AsyncAnthropic client."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


# -- Tests --

class TestAnthropicProviderProperties:
    """Test provider_name, model_name, and supports_tools properties."""

    def test_provider_name(self):
        provider = AnthropicProvider(client=MagicMock(), model="claude-haiku-4-5-20251001")
        assert provider.provider_name == "anthropic"

    def test_model_name(self):
        provider = AnthropicProvider(client=MagicMock(), model="claude-sonnet-4-5-20250929")
        assert provider.model_name == "claude-sonnet-4-5-20250929"

    def test_supports_tools(self):
        provider = AnthropicProvider(client=MagicMock(), model="claude-haiku-4-5-20251001")
        assert provider.supports_tools is True

    def test_satisfies_protocol(self):
        provider = AnthropicProvider(client=MagicMock(), model="claude-haiku-4-5-20251001")
        assert isinstance(provider, LLMProvider)


class TestAnthropicProviderSimpleChat:
    """Test simple text chat (no tools, no system prompt)."""

    async def test_simple_text_response(self):
        response = _make_response([_make_text_block("Hello, world!")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert isinstance(result, LLMResponse)
        assert result.text == "Hello, world!"
        assert result.stop_reason == "end"
        assert result.tool_calls is None

    async def test_multiple_text_blocks_joined(self):
        response = _make_response(
            [_make_text_block("Part 1"), _make_text_block("Part 2")],
            "end_turn",
        )
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.text == "Part 1\nPart 2"

    async def test_no_text_blocks_returns_empty_string(self):
        response = _make_response([], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.text == ""

    async def test_messages_passed_to_client(self):
        response = _make_response([_make_text_block("ok")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        messages = [{"role": "user", "content": "Hello"}]
        await provider.chat(messages=messages, max_tokens=512)

        call_kwargs = client.messages.create.call_args[1]
        assert call_kwargs["messages"] == messages
        assert call_kwargs["max_tokens"] == 512
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


class TestAnthropicProviderSystemPrompt:
    """Test that system prompts are passed with cache_control."""

    async def test_system_prompt_has_cache_control(self):
        response = _make_response([_make_text_block("ok")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            system="You are a helpful assistant.",
        )

        call_kwargs = client.messages.create.call_args[1]
        expected_system = [
            {
                "type": "text",
                "text": "You are a helpful assistant.",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        assert call_kwargs["system"] == expected_system

    async def test_no_system_prompt_omits_system_kwarg(self):
        response = _make_response([_make_text_block("ok")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        call_kwargs = client.messages.create.call_args[1]
        # system should not be passed at all (or be None)
        assert "system" not in call_kwargs or call_kwargs["system"] is None


class TestAnthropicProviderToolCalls:
    """Test tool call extraction from response."""

    async def test_tool_calls_extracted(self):
        response = _make_response(
            [
                _make_text_block("Let me check."),
                _make_tool_use_block("call_1", "get_status", {"name": "plex"}),
            ],
            "tool_use",
        )
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        result = await provider.chat(
            messages=[{"role": "user", "content": "How is plex?"}],
            tools=[{"name": "get_status", "description": "Get status", "input_schema": {}}],
        )

        assert result.stop_reason == "tool_use"
        assert result.text == "Let me check."
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1

        tc = result.tool_calls[0]
        assert isinstance(tc, ToolCall)
        assert tc.id == "call_1"
        assert tc.name == "get_status"
        assert tc.input == {"name": "plex"}

    async def test_multiple_tool_calls(self):
        response = _make_response(
            [
                _make_tool_use_block("call_1", "get_status", {"name": "plex"}),
                _make_tool_use_block("call_2", "get_logs", {"name": "radarr", "lines": 50}),
            ],
            "tool_use",
        )
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Check plex and radarr"}],
            tools=[
                {"name": "get_status", "description": "Get status", "input_schema": {}},
                {"name": "get_logs", "description": "Get logs", "input_schema": {}},
            ],
        )

        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].name == "get_status"
        assert result.tool_calls[1].name == "get_logs"

    async def test_tools_passed_with_cache_control_on_last(self):
        response = _make_response([_make_text_block("ok")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        tools = [
            {"name": "tool_a", "description": "A", "input_schema": {}},
            {"name": "tool_b", "description": "B", "input_schema": {}},
        ]

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
        )

        call_kwargs = client.messages.create.call_args[1]
        sent_tools = call_kwargs["tools"]

        # First tool should NOT have cache_control
        assert "cache_control" not in sent_tools[0]
        # Last tool should have cache_control
        assert sent_tools[-1]["cache_control"] == {"type": "ephemeral"}
        # Last tool should still have the original fields
        assert sent_tools[-1]["name"] == "tool_b"

    async def test_single_tool_gets_cache_control(self):
        response = _make_response([_make_text_block("ok")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        tools = [{"name": "only_tool", "description": "A", "input_schema": {}}]

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
        )

        call_kwargs = client.messages.create.call_args[1]
        sent_tools = call_kwargs["tools"]
        assert sent_tools[0]["cache_control"] == {"type": "ephemeral"}

    async def test_no_tools_omits_tools_kwarg(self):
        response = _make_response([_make_text_block("ok")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        call_kwargs = client.messages.create.call_args[1]
        assert "tools" not in call_kwargs or call_kwargs["tools"] is None

    async def test_original_tools_list_not_mutated(self):
        response = _make_response([_make_text_block("ok")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        tools = [
            {"name": "tool_a", "description": "A", "input_schema": {}},
            {"name": "tool_b", "description": "B", "input_schema": {}},
        ]

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
        )

        # Original tools list should not be modified
        assert "cache_control" not in tools[0]
        assert "cache_control" not in tools[1]


class TestAnthropicProviderToolResultTranslation:
    """Test that normalized tool_result messages are translated to Anthropic format."""

    async def test_tool_result_translated(self):
        response = _make_response([_make_text_block("Done")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        messages = [
            {"role": "user", "content": "Check plex"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "call_1", "name": "get_status", "input": {"name": "plex"}},
            ]},
            {"role": "tool_result", "tool_use_id": "call_1", "content": "plex is running"},
        ]

        await provider.chat(messages=messages)

        call_kwargs = client.messages.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        # First two messages should pass through
        assert sent_messages[0] == {"role": "user", "content": "Check plex"}
        assert sent_messages[1] == messages[1]  # assistant passes through

        # Third message (tool_result) should be translated to Anthropic format
        translated = sent_messages[2]
        assert translated["role"] == "user"
        assert len(translated["content"]) == 1
        assert translated["content"][0]["type"] == "tool_result"
        assert translated["content"][0]["tool_use_id"] == "call_1"
        assert translated["content"][0]["content"] == "plex is running"

    async def test_multiple_tool_results_grouped(self):
        """Multiple consecutive tool_result messages should each become a user message."""
        response = _make_response([_make_text_block("Done")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        messages = [
            {"role": "user", "content": "Check both"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_1", "name": "get_status", "input": {}},
                {"type": "tool_use", "id": "call_2", "name": "get_logs", "input": {}},
            ]},
            {"role": "tool_result", "tool_use_id": "call_1", "content": "running"},
            {"role": "tool_result", "tool_use_id": "call_2", "content": "no errors"},
        ]

        await provider.chat(messages=messages)

        call_kwargs = client.messages.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        # The two tool_results should be merged into a single user message
        # with two tool_result content blocks (Anthropic expects this)
        assert len(sent_messages) == 3  # user, assistant, user (with 2 tool_results)
        tool_msg = sent_messages[2]
        assert tool_msg["role"] == "user"
        assert len(tool_msg["content"]) == 2
        assert tool_msg["content"][0]["tool_use_id"] == "call_1"
        assert tool_msg["content"][1]["tool_use_id"] == "call_2"

    async def test_user_and_assistant_messages_pass_through(self):
        response = _make_response([_make_text_block("ok")], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        await provider.chat(messages=messages)

        call_kwargs = client.messages.create.call_args[1]
        sent_messages = call_kwargs["messages"]
        assert sent_messages == messages


class TestAnthropicProviderStopReasonMapping:
    """Test all stop reason mappings."""

    @pytest.mark.parametrize("anthropic_reason,expected", [
        ("end_turn", "end"),
        ("tool_use", "tool_use"),
        ("max_tokens", "max_tokens"),
        ("stop_sequence", "end"),
    ])
    async def test_stop_reason_mapped(self, anthropic_reason, expected):
        response = _make_response([_make_text_block("ok")], anthropic_reason)
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.stop_reason == expected

    async def test_unknown_stop_reason_passed_through(self):
        response = _make_response([_make_text_block("ok")], "something_new")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.stop_reason == "something_new"


class TestAnthropicProviderParsingGuards:
    """Tests for defensive parsing of edge-case responses."""

    async def test_none_content_returns_empty_response(self):
        response = MagicMock()
        response.content = None
        response.stop_reason = "end_turn"
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.text == ""
        assert result.stop_reason == "end"
        assert result.tool_calls is None

    async def test_empty_content_list_returns_empty_response(self):
        response = _make_response([], "end_turn")
        client = _make_client(response)
        provider = AnthropicProvider(client=client, model="claude-haiku-4-5-20251001")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.text == ""
        assert result.tool_calls is None
