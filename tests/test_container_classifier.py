"""Tests for container classifier."""

import pytest
from src.services.container_classifier import ContainerClassifier, ContainerClassification


@pytest.fixture
def classifier():
    return ContainerClassifier(anthropic_client=None)


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
