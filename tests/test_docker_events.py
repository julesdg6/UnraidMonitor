import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


def test_parse_container_from_docker_api():
    from src.monitors.docker_events import parse_container

    # Mock Docker container object
    mock_container = MagicMock()
    mock_container.name = "radarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/radarr:latest"]
    mock_container.attrs = {
        "State": {
            "Health": {"Status": "healthy"},
            "StartedAt": "2025-01-25T10:00:00.000000000Z",
        }
    }

    info = parse_container(mock_container)
    assert info.name == "radarr"
    assert info.status == "running"
    assert info.health == "healthy"
    assert info.image == "linuxserver/radarr:latest"


def test_parse_container_without_health_check():
    from src.monitors.docker_events import parse_container

    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/plex:latest"]
    mock_container.attrs = {
        "State": {
            "StartedAt": "2025-01-25T10:00:00.000000000Z",
        }
    }

    info = parse_container(mock_container)
    assert info.health is None


def test_parse_container_no_image_tags():
    from src.monitors.docker_events import parse_container

    mock_container = MagicMock()
    mock_container.name = "test"
    mock_container.status = "running"
    mock_container.image.tags = []
    mock_container.image.id = "sha256:abc123"
    mock_container.attrs = {"State": {}}

    info = parse_container(mock_container)
    assert info.image == "sha256:abc123"


def test_reconnect_calls_containers_list_once():
    """Reconnect should fetch container list once, not twice."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
    )

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/plex:latest"]
    mock_container.attrs = {"State": {"Health": {}}}
    mock_client.containers.list.return_value = [mock_container]

    # Patch DockerClient so _reconnect() returns our mock
    with patch("src.monitors.docker_events.docker.DockerClient", return_value=mock_client):
        monitor._reconnect()

    assert mock_client.containers.list.call_count == 1


def test_load_initial_state_uses_prefetched_containers():
    """load_initial_state should skip API call when containers are provided."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
    )

    mock_client = MagicMock()
    monitor._client = mock_client

    mock_container = MagicMock()
    mock_container.name = "sonarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/sonarr:latest"]
    mock_container.attrs = {"State": {}}

    monitor.load_initial_state(containers=[mock_container])

    # Should NOT call containers.list when pre-fetched list is passed
    mock_client.containers.list.assert_not_called()
    # Container should be loaded into state
    assert state.get("sonarr") is not None
    assert state.get("sonarr").status == "running"


def test_load_initial_state_fetches_when_no_containers_provided():
    """load_initial_state should call API when no containers are provided."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
    )

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.name = "radarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/radarr:latest"]
    mock_container.attrs = {"State": {}}
    mock_client.containers.list.return_value = [mock_container]
    monitor._client = mock_client

    monitor.load_initial_state()

    mock_client.containers.list.assert_called_once_with(all=True)
    assert state.get("radarr") is not None
