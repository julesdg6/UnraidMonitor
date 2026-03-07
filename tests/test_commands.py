import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_help_command_shows_category_buttons():
    """Test that /help shows category selection buttons."""
    from src.bot.commands import help_command

    handler = help_command()

    message = MagicMock()
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    call_args = message.answer.call_args
    text = call_args[0][0]
    assert "Commands" in text
    assert "category" in text.lower()

    # Should have inline keyboard with section buttons
    reply_markup = call_args.kwargs.get("reply_markup") or call_args[1].get("reply_markup")
    assert reply_markup is not None
    all_buttons = [b for row in reply_markup.inline_keyboard for b in row]
    button_texts = [b.text for b in all_buttons]
    assert any("Containers" in t for t in button_texts)
    assert any("Server" in t for t in button_texts)
    assert any("Alerts" in t for t in button_texts)
    assert any("Setup" in t for t in button_texts)


@pytest.mark.asyncio
async def test_help_section_callback():
    """Test clicking a help section button shows section content."""
    from src.bot.commands import help_section_callback

    handler = help_section_callback()

    callback = MagicMock()
    callback.data = "help:containers"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    call_args = callback.message.edit_text.call_args
    text = call_args[0][0]
    assert "Containers" in text
    assert "/status" in text
    assert "/logs" in text

    # Should have Back button
    reply_markup = call_args.kwargs.get("reply_markup") or call_args[1].get("reply_markup")
    assert reply_markup is not None
    all_buttons = [b for row in reply_markup.inline_keyboard for b in row]
    assert any("Back" in b.text for b in all_buttons)


@pytest.mark.asyncio
async def test_help_back_callback():
    """Test clicking Back returns to category overview."""
    from src.bot.commands import help_back_callback

    handler = help_back_callback()

    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    call_args = callback.message.edit_text.call_args
    text = call_args[0][0]
    assert "Commands" in text

    # Should have category buttons again
    reply_markup = call_args.kwargs.get("reply_markup") or call_args[1].get("reply_markup")
    assert reply_markup is not None
    all_buttons = [b for row in reply_markup.inline_keyboard for b in row]
    assert len(all_buttons) == 4  # 4 sections


@pytest.mark.asyncio
async def test_help_section_unknown():
    """Test unknown section returns error."""
    from src.bot.commands import help_section_callback

    handler = help_section_callback()

    callback = MagicMock()
    callback.data = "help:nonexistent"
    callback.answer = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once_with("Unknown section")


@pytest.mark.asyncio
async def test_status_command_shows_summary():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", "healthy", "img", None))
    state.update(ContainerInfo("radarr", "running", "unhealthy", "img", None))
    state.update(ContainerInfo("backup", "exited", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Running: 2" in response
    assert "Stopped: 1" in response
    assert "Unhealthy: 1" in response
    assert "backup" in response  # stopped container listed
    assert "radarr" in response  # unhealthy container listed


@pytest.mark.asyncio
async def test_status_command_shows_container_details():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.monitors.resource_monitor import ContainerStats
    from datetime import datetime, timezone, timedelta

    state = ContainerStateManager()
    started = datetime.now(timezone.utc) - timedelta(days=3, hours=14, minutes=22)
    state.update(ContainerInfo(
        "radarr", "running", "healthy",
        "linuxserver/radarr:latest",
        started,
    ))

    # Mock resource monitor
    mock_resource_monitor = MagicMock()
    mock_resource_monitor.get_container_stats = AsyncMock(return_value=ContainerStats(
        name="radarr",
        cpu_percent=12.3,
        memory_percent=45.2,
        memory_bytes=1_288_490_189,
        memory_limit=2_900_000_000,
        net_rx_bytes=1_610_612_736,
        net_tx_bytes=335_544_320,
        block_read_bytes=2_254_857_830,
        block_write_bytes=524_288_000,
        pids=42,
    ))

    handler = status_command(state, mock_resource_monitor)

    message = MagicMock()
    message.text = "/status radarr"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "running" in response.lower()
    assert "healthy" in response.lower()
    # Resource stats should be present
    assert "Resources" in response
    assert "12.3%" in response
    assert "45.2%" in response
    assert "Net I/O" in response
    assert "Block I/O" in response
    assert "PIDs: 42" in response
    # Uptime should be present
    assert "3d" in response
    assert "14h" in response


@pytest.mark.asyncio
async def test_status_command_stopped_container_no_resources():
    """Test that stopped containers don't show resource section."""
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("backup", "exited", None, "backup:latest", None))

    mock_resource_monitor = MagicMock()
    mock_resource_monitor.get_container_stats = AsyncMock()

    handler = status_command(state, mock_resource_monitor)

    message = MagicMock()
    message.text = "/status backup"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "backup" in response
    assert "exited" in response
    assert "Resources" not in response
    # Should not have called get_container_stats for stopped containers
    mock_resource_monitor.get_container_stats.assert_not_called()


@pytest.mark.asyncio
async def test_status_command_without_resource_monitor():
    """Test graceful degradation when resource_monitor is None."""
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from datetime import datetime, timezone

    state = ContainerStateManager()
    state.update(ContainerInfo(
        "radarr", "running", "healthy",
        "linuxserver/radarr:latest",
        datetime(2025, 1, 25, 10, 0, 0, tzinfo=timezone.utc),
    ))

    handler = status_command(state)  # No resource_monitor

    message = MagicMock()
    message.text = "/status radarr"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "running" in response.lower()
    assert "Resources" not in response


@pytest.mark.asyncio
async def test_status_command_partial_match():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status rad"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response


@pytest.mark.asyncio
async def test_status_command_multiple_matches():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "img", None))
    state.update(ContainerInfo("radarr-test", "running", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status radar"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "radarr-test" in response
    assert "multiple" in response.lower() or "matches" in response.lower()


@pytest.mark.asyncio
async def test_status_command_no_match():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    handler = status_command(state)

    message = MagicMock()
    message.text = "/status nonexistent"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "not found" in response.lower() or "no container" in response.lower()


@pytest.mark.asyncio
async def test_logs_command_returns_container_logs():
    """Test that /logs radarr returns logs with proper formatting."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    import docker

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", "healthy", "img", None))

    # Mock Docker client and container
    docker_client = MagicMock()
    docker_container = MagicMock()
    docker_container.logs.return_value = b"Log line 1\nLog line 2\nLog line 3\n"
    docker_client.containers.get.return_value = docker_container

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radarr"
    message.answer = AsyncMock()

    await handler(message)

    # Verify Docker was called correctly
    docker_client.containers.get.assert_called_once_with("radarr")
    docker_container.logs.assert_called_once_with(tail=20, timestamps=False)

    # Verify response
    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "Log line 1" in response
    assert "Log line 2" in response
    assert "Log line 3" in response


@pytest.mark.asyncio
async def test_logs_command_with_line_count():
    """Test that /logs radarr 50 calls logs(tail=50)."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", "healthy", "img", None))

    # Mock Docker client
    docker_client = MagicMock()
    docker_container = MagicMock()
    docker_container.logs.return_value = b"Test logs\n"
    docker_client.containers.get.return_value = docker_container

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radarr 50"
    message.answer = AsyncMock()

    await handler(message)

    # Verify tail=50 was used
    docker_container.logs.assert_called_once_with(tail=50, timestamps=False)


@pytest.mark.asyncio
async def test_logs_command_caps_at_100_lines():
    """Test that line count is capped at 100."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", "healthy", "img", None))

    # Mock Docker client
    docker_client = MagicMock()
    docker_container = MagicMock()
    docker_container.logs.return_value = b"Test logs\n"
    docker_client.containers.get.return_value = docker_container

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radarr 500"
    message.answer = AsyncMock()

    await handler(message)

    # Verify capped at 100
    docker_container.logs.assert_called_once_with(tail=100, timestamps=False)


@pytest.mark.asyncio
async def test_logs_command_container_not_found():
    """Test error message when container doesn't exist."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()

    docker_client = MagicMock()

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs nonexistent"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "not found" in response.lower() or "no container" in response.lower()


@pytest.mark.asyncio
async def test_logs_command_no_arguments():
    """Test usage message when no container specified."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    docker_client = MagicMock()

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Usage" in response or "usage" in response.lower()
    assert "Partial names" in response or "/logs radarr" in response


@pytest.mark.asyncio
async def test_logs_command_truncates_long_output():
    """Test that output over 4000 chars is truncated."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", "healthy", "img", None))

    # Mock Docker client with long logs
    docker_client = MagicMock()
    docker_container = MagicMock()
    long_log = "A" * 5000
    docker_container.logs.return_value = long_log.encode()
    docker_client.containers.get.return_value = docker_container

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radarr"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "truncated" in response.lower()


@pytest.mark.asyncio
async def test_logs_command_multiple_matches():
    """Test error message when multiple containers match."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "img", None))
    state.update(ContainerInfo("radarr-test", "running", None, "img", None))

    docker_client = MagicMock()

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radar"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "radarr-test" in response
    assert "multiple" in response.lower() or "matches" in response.lower()
