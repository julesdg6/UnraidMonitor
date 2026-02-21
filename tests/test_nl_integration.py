# tests/test_nl_integration.py
"""Integration tests for natural language chat feature."""
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from src.services.nl_processor import NLProcessor, MemoryStore
from src.services.nl_tools import NLToolExecutor, get_tool_definitions
from src.services.llm.provider import LLMResponse, ToolCall
from src.state import ContainerStateManager
from src.models import ContainerInfo


def make_mock_provider(response: LLMResponse | None = None):
    """Create a mock LLM provider."""
    provider = MagicMock()
    provider.supports_tools = True
    provider.model_name = "test-model"
    provider.provider_name = "test"
    provider.chat = AsyncMock(return_value=response or LLMResponse(
        text="OK",
        stop_reason="end",
        tool_calls=None,
    ))
    return provider


@pytest.fixture
def state():
    """Create a ContainerStateManager with test containers."""
    state = ContainerStateManager()
    state.update(ContainerInfo(
        name="plex",
        status="running",
        health="healthy",
        image="plexinc/pms-docker:latest",
        started_at=datetime.now(timezone.utc),
    ))
    state.update(ContainerInfo(
        name="radarr",
        status="running",
        health=None,
        image="linuxserver/radarr:latest",
        started_at=datetime.now(timezone.utc),
    ))
    state.update(ContainerInfo(
        name="mariadb",
        status="running",
        health="healthy",
        image="mariadb:10",
        started_at=datetime.now(timezone.utc),
    ))
    return state


@pytest.fixture
def mock_docker():
    """Create a mock Docker client."""
    docker = Mock()
    container = Mock()
    container.logs.return_value = b"[INFO] Server started\n[ERROR] Connection timeout\n"
    docker.containers.get.return_value = container
    return docker


@pytest.fixture
def executor(state, mock_docker):
    """Create an NLToolExecutor with test state and mock Docker."""
    return NLToolExecutor(
        state=state,
        docker_client=mock_docker,
        protected_containers=["mariadb"],
    )


class TestNLIntegration:
    """Integration tests for the NL chat feature end-to-end flows."""

    @pytest.mark.asyncio
    async def test_end_to_end_status_query(self, executor):
        """Test a complete flow: user asks about container status."""
        mock_provider = make_mock_provider()

        # First response: tool use
        tc = ToolCall(id="123", name="get_container_status", input={"name": "plex"})
        response1 = LLMResponse(text="", stop_reason="tool_use", tool_calls=[tc])

        # Second response: final text
        response2 = LLMResponse(text="Plex is running and healthy.", stop_reason="end")

        mock_provider.chat = AsyncMock(side_effect=[response1, response2])

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="how's plex doing?")

        assert "plex" in result.response.lower() or "running" in result.response.lower()

    @pytest.mark.asyncio
    async def test_followup_uses_context(self, executor):
        """Test that follow-up questions use conversation context."""
        mock_provider = make_mock_provider(LLMResponse(text="OK", stop_reason="end"))

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=executor,
        )

        # First query
        await processor.process(user_id=123, message="check plex")

        # Second query (follow-up)
        await processor.process(user_id=123, message="what about its logs?")

        # Check that second call included history
        calls = mock_provider.chat.call_args_list
        second_call_messages = calls[1][1]["messages"]
        # Should have: previous user msg, previous assistant msg, new user msg
        assert len(second_call_messages) >= 3

    @pytest.mark.asyncio
    async def test_protected_container_rejection(self, executor):
        """Test that protected containers cannot be controlled."""
        result = await executor.execute("restart_container", {"name": "mariadb"})

        assert "protected" in result.lower() or "cannot" in result.lower()

    @pytest.mark.asyncio
    async def test_action_returns_confirmation(self, executor):
        """Test that actions return confirmation needed."""
        result = await executor.execute("restart_container", {"name": "plex"})

        assert result.startswith("CONFIRMATION_NEEDED:")

    @pytest.mark.asyncio
    async def test_start_executes_immediately(self, executor):
        """Test that start doesn't require confirmation."""
        mock_controller = Mock()
        mock_controller.start = AsyncMock(return_value="Started plex")
        executor._controller = mock_controller

        result = await executor.execute("start_container", {"name": "plex"})

        assert "CONFIRMATION_NEEDED" not in result
        mock_controller.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_loop_with_multiple_tools(self, executor):
        """Test that processor handles multiple tool calls in sequence."""
        mock_provider = make_mock_provider()

        # First response: get status
        tc1 = ToolCall(id="1", name="get_container_status", input={"name": "plex"})
        response1 = LLMResponse(text="", stop_reason="tool_use", tool_calls=[tc1])

        # Second response: get logs
        tc2 = ToolCall(id="2", name="get_container_logs", input={"name": "plex"})
        response2 = LLMResponse(text="", stop_reason="tool_use", tool_calls=[tc2])

        # Third response: final text
        response3 = LLMResponse(text="Plex is running. Logs show a connection timeout error.", stop_reason="end")

        mock_provider.chat = AsyncMock(side_effect=[response1, response2, response3])

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="what's wrong with plex?")

        # Should have called API 3 times
        assert mock_provider.chat.call_count == 3
        assert "plex" in result.response.lower() or "connection" in result.response.lower()

    @pytest.mark.asyncio
    async def test_confirmation_stored_in_memory(self, executor):
        """Test that pending actions are stored in memory."""
        mock_provider = make_mock_provider()

        # Response with tool use for restart
        tc = ToolCall(id="123", name="restart_container", input={"name": "plex"})
        response1 = LLMResponse(text="", stop_reason="tool_use", tool_calls=[tc])

        # Final text response
        response2 = LLMResponse(text="I can restart plex for you. Please confirm.", stop_reason="end")

        mock_provider.chat = AsyncMock(side_effect=[response1, response2])

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="restart plex")

        # Check pending action is set
        assert result.pending_action is not None
        assert result.pending_action["action"] == "restart"
        assert result.pending_action["container"] == "plex"

        # Check it's also in memory
        memory = processor.memory_store.get(123)
        assert memory is not None
        assert memory.pending_action == result.pending_action

    @pytest.mark.asyncio
    async def test_different_users_have_separate_contexts(self, executor):
        """Test that different users have separate conversation memories."""
        mock_provider = make_mock_provider(LLMResponse(text="OK", stop_reason="end"))

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=executor,
        )

        # User 1 sends message
        await processor.process(user_id=100, message="check plex")

        # User 2 sends message
        await processor.process(user_id=200, message="check radarr")

        # Verify separate memories
        memory_1 = processor.memory_store.get(100)
        memory_2 = processor.memory_store.get(200)

        assert memory_1 is not None
        assert memory_2 is not None
        assert memory_1 is not memory_2
        assert "plex" in memory_1.messages[0]["content"]
        assert "radarr" in memory_2.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_error_handling_returns_fallback(self, executor):
        """Test that errors during processing return a fallback message."""
        mock_provider = make_mock_provider()
        mock_provider.chat = AsyncMock(side_effect=Exception("API error"))

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="check plex")

        # Should return a user-friendly error message
        assert "sorry" in result.response.lower() or "couldn't" in result.response.lower()

    @pytest.mark.asyncio
    async def test_no_anthropic_client_returns_error(self, executor):
        """Test that missing provider returns helpful message."""
        processor = NLProcessor(
            provider=None,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="check plex")

        assert "not configured" in result.response.lower()

    @pytest.mark.asyncio
    async def test_container_not_found_returns_error(self, executor):
        """Test that non-existent container returns error message."""
        result = await executor.execute("get_container_status", {"name": "nonexistent"})

        assert "not found" in result.lower() or "no container" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_protected_container_rejected(self, executor):
        """Test that stopping protected containers is rejected."""
        result = await executor.execute("stop_container", {"name": "mariadb"})

        assert "protected" in result.lower() or "cannot" in result.lower()

    @pytest.mark.asyncio
    async def test_pull_protected_container_rejected(self, executor):
        """Test that pulling protected containers is rejected."""
        result = await executor.execute("pull_container", {"name": "mariadb"})

        assert "protected" in result.lower() or "cannot" in result.lower()

    @pytest.mark.asyncio
    async def test_get_container_list_includes_all(self, state, mock_docker):
        """Test that container list includes all containers."""
        executor = NLToolExecutor(
            state=state,
            docker_client=mock_docker,
            protected_containers=[],
        )

        result = await executor.execute("get_container_list", {})

        assert "plex" in result
        assert "radarr" in result
        assert "mariadb" in result

    @pytest.mark.asyncio
    async def test_logs_retrieval(self, executor):
        """Test that logs are retrieved correctly."""
        result = await executor.execute("get_container_logs", {"name": "plex"})

        # Should contain log content
        assert "Server started" in result or "Connection timeout" in result

    @pytest.mark.asyncio
    async def test_new_message_clears_pending_action(self, executor):
        """Test that a new message clears any pending action."""
        mock_provider = make_mock_provider()

        # First: action that needs confirmation
        tc = ToolCall(id="1", name="restart_container", input={"name": "plex"})
        response1 = LLMResponse(text="", stop_reason="tool_use", tool_calls=[tc])
        response2 = LLMResponse(text="Confirm restart?", stop_reason="end")

        # Second: simple query (no confirmation)
        response3 = LLMResponse(text="Everything is fine.", stop_reason="end")

        mock_provider.chat = AsyncMock(side_effect=[response1, response2, response3])

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=executor,
        )

        # First message triggers confirmation
        result1 = await processor.process(user_id=123, message="restart plex")
        assert result1.pending_action is not None

        # Second message should clear pending action
        result2 = await processor.process(user_id=123, message="how are things?")
        # After new message, memory's pending action should be cleared
        memory = processor.memory_store.get(123)
        # Either result2 has no pending_action, or memory was cleared (result2 has None)
        assert result2.pending_action is None


@pytest.mark.asyncio
async def test_yes_falls_through_to_nl_when_no_pending_state():
    """When no confirmation or diagnostic is pending, 'yes' should not be consumed by YesFilter or DetailsFilter."""
    from unittest.mock import MagicMock
    from src.bot.telegram_bot import YesFilter, DetailsFilter
    from src.bot.confirmation import ConfirmationManager

    # Set up filters with no pending state
    confirmation = ConfirmationManager()
    yes_filter = YesFilter(confirmation)

    mock_diagnostic = MagicMock()
    mock_diagnostic.has_pending.return_value = False
    details_filter = DetailsFilter(mock_diagnostic)

    # Create a "yes" message
    message = MagicMock()
    message.text = "yes"
    message.from_user = MagicMock()
    message.from_user.id = 123

    # Neither filter should match
    assert await yes_filter(message) is False
    assert await details_filter(message) is False

    # NLFilter should still match it
    from src.bot.nl_handler import NLFilter
    nl_filter = NLFilter()
    assert await nl_filter(message) is True
