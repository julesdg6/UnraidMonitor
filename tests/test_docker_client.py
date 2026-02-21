"""Tests for SharedDockerClient wrapper."""

from unittest.mock import MagicMock


class TestSharedDockerClient:
    def test_get_client_returns_current(self):
        from src.services.docker_client import SharedDockerClient

        mock_client = MagicMock()
        shared = SharedDockerClient(mock_client)
        assert shared.client is mock_client

    def test_replace_client_updates_reference(self):
        from src.services.docker_client import SharedDockerClient

        old = MagicMock()
        new = MagicMock()
        shared = SharedDockerClient(old)
        shared.replace(new)
        assert shared.client is new

    def test_containers_delegates_to_current_client(self):
        from src.services.docker_client import SharedDockerClient

        mock_client = MagicMock()
        shared = SharedDockerClient(mock_client)
        _ = shared.containers.list()
        mock_client.containers.list.assert_called_once()

    def test_images_delegates_to_current_client(self):
        from src.services.docker_client import SharedDockerClient

        mock_client = MagicMock()
        shared = SharedDockerClient(mock_client)
        _ = shared.images.list()
        mock_client.images.list.assert_called_once()

    def test_close_delegates(self):
        from src.services.docker_client import SharedDockerClient

        mock_client = MagicMock()
        shared = SharedDockerClient(mock_client)
        shared.close()
        mock_client.close.assert_called_once()

    def test_replace_updates_containers_delegation(self):
        """After replace, containers delegates to the NEW client."""
        from src.services.docker_client import SharedDockerClient

        old = MagicMock()
        new = MagicMock()
        shared = SharedDockerClient(old)
        shared.replace(new)
        _ = shared.containers.list()
        new.containers.list.assert_called_once()
        old.containers.list.assert_not_called()
