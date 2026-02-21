import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from src.services.llm.provider import LLMResponse


def make_mock_provider(text=""):
    """Create a mock LLM provider returning the given text."""
    provider = MagicMock()
    provider.supports_tools = False
    provider.model_name = "test-model"
    provider.provider_name = "test"
    provider.chat = AsyncMock(return_value=LLMResponse(
        text=text,
        stop_reason="end",
        tool_calls=None,
    ))
    return provider


def test_diagnostic_context_creation():
    """Test DiagnosticContext dataclass creation."""
    from src.services.diagnostic import DiagnosticContext

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Error: connection refused",
        exit_code=1,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=2,
        brief_summary="Container crashed due to database connection failure.",
    )

    assert context.container_name == "overseerr"
    assert context.exit_code == 1
    assert context.restart_count == 2
    assert "database" in context.brief_summary


@pytest.mark.asyncio
async def test_diagnostic_service_gathers_context():
    """Test gathering container context from Docker."""
    from src.services.diagnostic import DiagnosticService

    # Mock Docker container
    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error: connection refused\nRetrying..."
    mock_container.attrs = {
        "State": {
            "ExitCode": 1,
            "StartedAt": "2025-01-25T10:00:00Z",
        },
        "RestartCount": 2,
    }
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    service = DiagnosticService(docker_client=mock_client, provider=None)

    context = await service.gather_context("overseerr", lines=50)

    assert context.container_name == "overseerr"
    assert context.exit_code == 1
    assert context.restart_count == 2
    assert "Error: connection refused" in context.logs
    assert context.image == "linuxserver/overseerr:latest"


@pytest.mark.asyncio
async def test_diagnostic_service_handles_missing_container():
    """Test handling container not found."""
    import docker
    from src.services.diagnostic import DiagnosticService

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

    service = DiagnosticService(docker_client=mock_client, provider=None)

    context = await service.gather_context("nonexistent", lines=50)

    assert context is None


@pytest.mark.asyncio
async def test_diagnostic_service_analyzes_with_claude():
    """Test calling AI provider for analysis."""
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    mock_client = MagicMock()
    mock_provider = make_mock_provider(
        "The container crashed due to OOM. Increase memory limits."
    )

    service = DiagnosticService(docker_client=mock_client, provider=mock_provider)

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Error: JavaScript heap out of memory",
        exit_code=137,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=2,
    )

    result = await service.analyze(context)

    assert "OOM" in result or "memory" in result.lower()
    mock_provider.chat.assert_called_once()


@pytest.mark.asyncio
async def test_diagnostic_service_stores_and_retrieves_context():
    """Test storing context for follow-up."""
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    mock_client = MagicMock()
    mock_provider = make_mock_provider(
        "Detailed analysis: The root cause is..."
    )

    service = DiagnosticService(docker_client=mock_client, provider=mock_provider)

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Error log",
        exit_code=1,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=0,
        brief_summary="Container crashed.",
    )

    # Store context for user
    service.store_context(user_id=123, context=context)

    # Check pending
    assert service.has_pending(123) is True
    assert service.has_pending(456) is False

    # Get details
    details = await service.get_details(123)

    assert details is not None
    assert "root cause" in details.lower() or "Detailed" in details

    # Context should be cleared after retrieval
    assert service.has_pending(123) is False
