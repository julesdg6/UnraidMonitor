"""Tests for container classifier."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from src.services.container_classifier import ContainerClassifier, ContainerClassification
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


@pytest.fixture
def classifier():
    return ContainerClassifier(provider=None)


class TestPatternMatching:
    def test_database_classified_as_priority_and_watched(self, classifier):
        result = classifier.classify_by_pattern("mariadb", "linuxserver/mariadb")
        assert "priority" in result.categories
        assert "watched" in result.categories

    def test_arr_stack_classified_as_watched(self, classifier):
        result = classifier.classify_by_pattern("radarr", "linuxserver/radarr")
        assert "watched" in result.categories
        assert "priority" not in result.categories

    def test_bot_self_classified_as_protected(self, classifier):
        result = classifier.classify_by_pattern("unraid-monitor-bot", "unraid-monitor-bot:latest")
        assert "protected" in result.categories

    def test_download_client_classified_as_watched_and_killable(self, classifier):
        result = classifier.classify_by_pattern("qbit", "linuxserver/qbittorrent")
        assert "watched" in result.categories
        assert "killable" in result.categories

    def test_unknown_container_unclassified(self, classifier):
        result = classifier.classify_by_pattern("my-custom-app", "myrepo/custom:latest")
        assert len(result.categories) == 0

    def test_image_name_used_for_matching(self, classifier):
        result = classifier.classify_by_pattern("dl", "linuxserver/qbittorrent")
        assert "watched" in result.categories
        assert "killable" in result.categories

    def test_media_apps_classified_as_watched(self, classifier):
        result = classifier.classify_by_pattern("plex", "plexinc/plex-media-server")
        assert "watched" in result.categories

    def test_case_insensitive(self, classifier):
        result = classifier.classify_by_pattern("MariaDB", "LinuxServer/MariaDB")
        assert "priority" in result.categories


class TestAIClassification:
    @pytest.mark.asyncio
    async def test_ai_classifies_unknown_containers(self):
        mock_provider = make_mock_provider(
            '[{"name": "bookstack", "categories": ["watched"],'
            ' "description": "Wiki/documentation platform"},'
            ' {"name": "dozzle", "categories": ["ignored"],'
            ' "description": "Log viewer UI"}]'
        )

        classifier = ContainerClassifier(provider=mock_provider)
        unclassified = [
            ContainerClassification(name="bookstack", image="linuxserver/bookstack"),
            ContainerClassification(name="dozzle", image="amir20/dozzle"),
        ]
        results = await classifier.classify_batch_with_ai(unclassified)

        assert len(results) == 2
        bookstack = next(r for r in results if r.name == "bookstack")
        assert "watched" in bookstack.categories
        assert bookstack.ai_suggested is True
        assert bookstack.description == "Wiki/documentation platform"

    @pytest.mark.asyncio
    async def test_ai_returns_originals_on_failure(self):
        mock_provider = MagicMock()
        mock_provider.supports_tools = False
        mock_provider.model_name = "test-model"
        mock_provider.provider_name = "test"
        mock_provider.chat = AsyncMock(side_effect=Exception("API error"))

        classifier = ContainerClassifier(provider=mock_provider)
        unclassified = [
            ContainerClassification(name="bookstack", image="linuxserver/bookstack")
        ]
        results = await classifier.classify_batch_with_ai(unclassified)

        assert len(results) == 1
        assert len(results[0].categories) == 0

    @pytest.mark.asyncio
    async def test_ai_skipped_without_client(self):
        classifier = ContainerClassifier(provider=None)
        unclassified = [
            ContainerClassification(name="bookstack", image="linuxserver/bookstack")
        ]
        results = await classifier.classify_batch_with_ai(unclassified)

        assert len(results) == 1
        assert len(results[0].categories) == 0

    @pytest.mark.asyncio
    async def test_ai_filters_invalid_categories(self):
        mock_provider = make_mock_provider(
            '[{"name": "app", "categories": ["watched", "superadmin"],'
            ' "description": "An app"}]'
        )

        classifier = ContainerClassifier(provider=mock_provider)
        unclassified = [ContainerClassification(name="app", image="myrepo/app")]
        results = await classifier.classify_batch_with_ai(unclassified)

        assert "watched" in results[0].categories
        assert "superadmin" not in results[0].categories


class TestClassifyAll:
    @pytest.mark.asyncio
    async def test_classify_all_combines_pattern_and_ai(self):
        mock_provider = make_mock_provider(
            '[{"name": "bookstack", "categories": ["watched"],'
            ' "description": "Wiki"}]'
        )

        classifier = ContainerClassifier(provider=mock_provider)
        containers = [
            ("mariadb", "linuxserver/mariadb", "running"),
            ("radarr", "linuxserver/radarr", "running"),
            ("bookstack", "linuxserver/bookstack", "running"),
        ]
        results = await classifier.classify_all(containers)

        mariadb = next(r for r in results if r.name == "mariadb")
        assert "priority" in mariadb.categories
        assert mariadb.ai_suggested is False

        radarr = next(r for r in results if r.name == "radarr")
        assert "watched" in radarr.categories

        bookstack = next(r for r in results if r.name == "bookstack")
        assert "watched" in bookstack.categories
        assert bookstack.ai_suggested is True

    @pytest.mark.asyncio
    async def test_classify_all_no_ai_call_when_all_matched(self):
        mock_provider = make_mock_provider()

        classifier = ContainerClassifier(provider=mock_provider)
        containers = [
            ("mariadb", "linuxserver/mariadb", "running"),
            ("plex", "plexinc/plex-media-server", "running"),
        ]
        await classifier.classify_all(containers)

        mock_provider.chat.assert_not_called()
