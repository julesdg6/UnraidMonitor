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
