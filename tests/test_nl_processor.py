# tests/test_nl_processor.py
import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.services.nl_processor import ConversationMemory, MemoryStore, NLProcessor, ProcessResult
from src.services.llm.provider import LLMResponse, ToolCall


def make_mock_provider(response: LLMResponse | None = None):
    """Create a mock LLM provider."""
    provider = MagicMock()
    provider.supports_tools = True
    provider.model_name = "test-model"
    provider.provider_name = "test"
    provider.chat = AsyncMock(return_value=response or LLMResponse(
        text="Everything looks fine!",
        stop_reason="end",
        tool_calls=None,
    ))
    return provider


class TestConversationMemory:
    def test_add_exchange_stores_messages(self):
        memory = ConversationMemory(user_id=123)
        memory.add_exchange("what's wrong?", "everything is fine")

        assert len(memory.messages) == 2
        assert memory.messages[0]["role"] == "user"
        assert memory.messages[0]["content"] == "what's wrong?"
        assert memory.messages[1]["role"] == "assistant"
        assert memory.messages[1]["content"] == "everything is fine"

    def test_add_exchange_trims_to_max(self):
        memory = ConversationMemory(user_id=123, max_exchanges=2)

        memory.add_exchange("q1", "a1")
        memory.add_exchange("q2", "a2")
        memory.add_exchange("q3", "a3")  # Should push out q1/a1

        assert len(memory.messages) == 4  # 2 exchanges = 4 messages
        assert memory.messages[0]["content"] == "q2"

    def test_get_messages_returns_copy(self):
        memory = ConversationMemory(user_id=123)
        memory.add_exchange("q", "a")

        messages = memory.get_messages()
        messages.append({"role": "user", "content": "injected"})

        assert len(memory.messages) == 2  # Original unchanged

    def test_clear_removes_all_messages(self):
        memory = ConversationMemory(user_id=123)
        memory.add_exchange("q", "a")
        memory.clear()

        assert len(memory.messages) == 0

    def test_pending_action_initially_none(self):
        memory = ConversationMemory(user_id=123)
        assert memory.pending_action is None

    def test_set_and_get_pending_action(self):
        memory = ConversationMemory(user_id=123)
        memory.pending_action = {"action": "restart", "container": "plex"}

        assert memory.pending_action == {"action": "restart", "container": "plex"}

    def test_clear_also_clears_pending_action(self):
        memory = ConversationMemory(user_id=123)
        memory.pending_action = {"action": "restart", "container": "plex"}
        memory.clear()

        assert memory.pending_action is None

    def test_messages_is_deque(self):
        from collections import deque
        memory = ConversationMemory(user_id=123)
        assert isinstance(memory.messages, deque)


class TestMemoryStore:
    def test_get_or_create_creates_new_memory(self):
        store = MemoryStore()
        memory = store.get_or_create(123)

        assert memory.user_id == 123
        assert len(memory.messages) == 0

    def test_get_or_create_returns_existing_memory(self):
        store = MemoryStore()
        memory1 = store.get_or_create(123)
        memory1.add_exchange("q", "a")

        memory2 = store.get_or_create(123)

        assert memory2 is memory1
        assert len(memory2.messages) == 2

    def test_get_returns_none_for_unknown_user(self):
        store = MemoryStore()
        assert store.get(999) is None

    def test_clear_user_removes_memory(self):
        store = MemoryStore()
        store.get_or_create(123)
        store.clear_user(123)

        assert store.get(123) is None


class TestNLProcessor:
    @pytest.fixture
    def mock_provider(self):
        return make_mock_provider(LLMResponse(
            text="Everything looks fine!",
            stop_reason="end",
            tool_calls=None,
        ))

    @pytest.fixture
    def mock_executor(self):
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="Container: plex\nStatus: running")
        return executor

    @pytest.fixture
    def processor(self, mock_provider, mock_executor):
        return NLProcessor(
            provider=mock_provider,
            tool_executor=mock_executor,
        )

    @pytest.mark.asyncio
    async def test_process_simple_query(self, processor):
        result = await processor.process(user_id=123, message="how's everything?")
        assert result.response is not None
        assert len(result.response) > 0

    @pytest.mark.asyncio
    async def test_process_stores_in_memory(self, processor):
        await processor.process(user_id=123, message="check plex")
        memory = processor.memory_store.get(123)
        assert memory is not None
        assert len(memory.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_process_uses_conversation_history(self, processor, mock_provider):
        # First message
        await processor.process(user_id=123, message="check plex")
        # Second message (should include history)
        await processor.process(user_id=123, message="restart it")
        # Check that the second call included history
        calls = mock_provider.chat.call_args_list
        assert len(calls) == 2
        # Second call should have more messages (history + new)
        second_call_messages = calls[1][1]["messages"]
        assert len(second_call_messages) >= 2

    @pytest.mark.asyncio
    async def test_process_returns_pending_action_for_confirmation(self, processor, mock_provider, mock_executor):
        # Mock tool use response
        tool_call = ToolCall(id="123", name="restart_container", input={"name": "plex"})
        response1 = LLMResponse(text="", stop_reason="tool_use", tool_calls=[tool_call])
        # Mock executor returning confirmation needed
        mock_executor.execute = AsyncMock(return_value="CONFIRMATION_NEEDED:restart:plex")
        # Mock final response
        response2 = LLMResponse(text="I can restart plex for you.", stop_reason="end")
        mock_provider.chat = AsyncMock(side_effect=[response1, response2])

        result = await processor.process(user_id=123, message="restart plex")
        assert result.pending_action is not None
        assert result.pending_action["action"] == "restart"
        assert result.pending_action["container"] == "plex"

    @pytest.mark.asyncio
    async def test_process_without_anthropic_returns_error(self):
        processor = NLProcessor(provider=None, tool_executor=Mock())
        result = await processor.process(user_id=123, message="hello")
        assert "not configured" in result.response.lower() or "not available" in result.response.lower()


def test_system_prompt_instructs_tool_use_for_actions():
    """System prompt should tell Claude to use tools rather than suggest actions textually."""
    from src.services.nl_processor import SYSTEM_PROMPT
    assert "call the appropriate tool" in SYSTEM_PROMPT.lower()


def test_per_user_rate_limiter_uses_deque():
    """Rate limiter should use deque internally for O(1) eviction."""
    from collections import deque
    from src.utils.rate_limiter import PerUserRateLimiter

    limiter = PerUserRateLimiter(max_per_minute=5, max_per_hour=20)
    limiter.is_allowed(1)

    assert isinstance(limiter._minute_timestamps[1], deque)
    assert isinstance(limiter._hour_timestamps[1], deque)
