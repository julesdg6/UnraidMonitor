"""Tests for BaseMuteManager persistence and expiry logic."""

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from src.alerts.base_mute_manager import BaseMuteManager


class ConcreteMuteManager(BaseMuteManager):
    """Concrete implementation for testing BaseMuteManager."""

    def is_muted(self, key: str) -> bool:
        return self._is_muted(key)

    def add_mute(self, key: str, duration: timedelta) -> datetime:
        return self._add_mute(key, duration)

    def remove_mute(self, key: str) -> bool:
        return self._remove_mute(key)

    def get_active_mutes(self) -> list[tuple[str, datetime]]:
        return self._get_active_mutes()


class TestBaseMuteManagerPersistence:
    """Tests for JSON persistence."""

    def test_save_creates_parent_directories(self, tmp_path):
        """Test that save creates parent directories if missing."""
        nested_path = tmp_path / "nested" / "deep" / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(nested_path))

        manager.add_mute("test", timedelta(hours=1))

        assert nested_path.exists()

    def test_save_writes_valid_json(self, tmp_path):
        """Test that mutes are saved as valid JSON."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        manager.add_mute("plex", timedelta(hours=1))
        manager.add_mute("radarr", timedelta(hours=2))

        # Read the raw JSON
        with open(json_file) as f:
            data = json.load(f)

        assert "plex" in data
        assert "radarr" in data
        # Verify ISO format
        datetime.fromisoformat(data["plex"])
        datetime.fromisoformat(data["radarr"])

    def test_load_reads_existing_mutes(self, tmp_path):
        """Test that mutes are loaded from existing JSON file."""
        json_file = tmp_path / "mutes.json"
        future_time = (datetime.now() + timedelta(hours=1)).isoformat()

        # Write JSON manually
        with open(json_file, "w") as f:
            json.dump({"plex": future_time}, f)

        manager = ConcreteMuteManager(json_path=str(json_file))

        assert manager.is_muted("plex")

    def test_load_handles_missing_file(self, tmp_path):
        """Test that missing JSON file is handled gracefully."""
        json_file = tmp_path / "nonexistent.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        assert manager.get_active_mutes() == []

    def test_load_handles_invalid_json(self, tmp_path):
        """Test that invalid JSON is handled gracefully."""
        json_file = tmp_path / "mutes.json"

        # Write invalid JSON
        with open(json_file, "w") as f:
            f.write("not valid json {{{")

        manager = ConcreteMuteManager(json_path=str(json_file))

        # Should start with empty mutes
        assert manager.get_active_mutes() == []

    def test_load_handles_invalid_datetime(self, tmp_path):
        """Test that invalid datetime values are handled gracefully."""
        json_file = tmp_path / "mutes.json"

        # Write valid JSON with invalid datetime
        with open(json_file, "w") as f:
            json.dump({"plex": "not-a-datetime"}, f)

        manager = ConcreteMuteManager(json_path=str(json_file))

        # Should start with empty mutes
        assert manager.get_active_mutes() == []

    def test_clean_expired_does_not_save_immediately(self, tmp_path):
        """Automated cleanup should not trigger immediate disk write."""
        import unittest.mock
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        # Add a mute that's already expired
        manager._mutes["expired"] = datetime.now() - timedelta(hours=1)
        manager._save()  # Save to disk first

        # Track save calls
        with unittest.mock.patch.object(manager, '_save') as mock_save:
            manager.get_active_mutes()  # Triggers _clean_expired
            mock_save.assert_not_called()

        # But the mute should be removed from memory
        assert "expired" not in manager._mutes

    def test_persistence_survives_restart(self, tmp_path):
        """Test that mutes persist across manager restarts."""
        json_file = tmp_path / "mutes.json"

        # Create manager and add mute
        manager1 = ConcreteMuteManager(json_path=str(json_file))
        manager1.add_mute("plex", timedelta(hours=1))

        # Create new manager with same file
        manager2 = ConcreteMuteManager(json_path=str(json_file))

        assert manager2.is_muted("plex")


class TestBaseMuteManagerExpiry:
    """Tests for mute expiry logic."""

    def test_is_muted_returns_true_for_active_mute(self, tmp_path):
        """Test that active mutes return True."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        manager.add_mute("plex", timedelta(hours=1))

        assert manager.is_muted("plex")

    def test_is_muted_returns_false_for_expired_mute(self, tmp_path):
        """Test that expired mutes return False."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        # Add expired mute directly
        manager._mutes["plex"] = datetime.now() - timedelta(minutes=5)

        assert not manager.is_muted("plex")

    def test_is_muted_cleans_up_expired_mute(self, tmp_path):
        """Test that checking an expired mute removes it."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        # Add expired mute directly
        manager._mutes["plex"] = datetime.now() - timedelta(minutes=5)

        # Check it (should clean up)
        manager.is_muted("plex")

        # Verify it's removed from internal dict
        assert "plex" not in manager._mutes

    def test_is_muted_returns_false_for_unknown_key(self, tmp_path):
        """Test that unknown keys return False."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        assert not manager.is_muted("unknown")

    def test_clean_expired_removes_multiple_expired(self, tmp_path):
        """Test that _clean_expired removes all expired mutes."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        now = datetime.now()
        manager._mutes["expired1"] = now - timedelta(hours=1)
        manager._mutes["expired2"] = now - timedelta(hours=2)
        manager._mutes["active"] = now + timedelta(hours=1)

        manager._clean_expired()

        assert "expired1" not in manager._mutes
        assert "expired2" not in manager._mutes
        assert "active" in manager._mutes

    def test_get_active_mutes_excludes_expired(self, tmp_path):
        """Test that get_active_mutes only returns non-expired mutes."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        now = datetime.now()
        manager._mutes["expired"] = now - timedelta(hours=1)
        manager._mutes["active"] = now + timedelta(hours=1)

        mutes = manager.get_active_mutes()

        keys = [k for k, _ in mutes]
        assert "expired" not in keys
        assert "active" in keys


class TestBaseMuteManagerOperations:
    """Tests for add/remove mute operations."""

    def test_add_mute_returns_expiry_time(self, tmp_path):
        """Test that add_mute returns the expiry datetime."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        before = datetime.now()
        expiry = manager.add_mute("plex", timedelta(hours=1))
        after = datetime.now()

        # Expiry should be ~1 hour from now
        expected_min = before + timedelta(hours=1)
        expected_max = after + timedelta(hours=1)

        assert expected_min <= expiry <= expected_max

    def test_add_mute_overwrites_existing(self, tmp_path):
        """Test that adding a mute for an existing key overwrites it."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        expiry1 = manager.add_mute("plex", timedelta(hours=1))
        expiry2 = manager.add_mute("plex", timedelta(hours=5))

        assert expiry2 > expiry1
        assert manager._mutes["plex"] == expiry2

    def test_remove_mute_returns_true_if_existed(self, tmp_path):
        """Test that remove_mute returns True if mute existed."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        manager.add_mute("plex", timedelta(hours=1))

        assert manager.remove_mute("plex") is True
        assert not manager.is_muted("plex")

    def test_remove_mute_returns_false_if_not_found(self, tmp_path):
        """Test that remove_mute returns False if mute didn't exist."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        assert manager.remove_mute("nonexistent") is False

    def test_remove_mute_persists_change(self, tmp_path):
        """Test that removing a mute saves to JSON."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        manager.add_mute("plex", timedelta(hours=1))
        manager.remove_mute("plex")

        # Check JSON file
        with open(json_file) as f:
            data = json.load(f)

        assert "plex" not in data

    def test_get_active_mutes_returns_key_expiry_pairs(self, tmp_path):
        """Test that get_active_mutes returns list of (key, expiry) tuples."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        manager.add_mute("plex", timedelta(hours=1))
        manager.add_mute("radarr", timedelta(hours=2))

        mutes = manager.get_active_mutes()

        assert len(mutes) == 2
        keys = [k for k, _ in mutes]
        assert "plex" in keys
        assert "radarr" in keys

        # Verify expiry times are datetime objects
        for key, expiry in mutes:
            assert isinstance(expiry, datetime)


class TestBaseMuteManagerEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_key(self, tmp_path):
        """Test handling of empty key string."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        # Should work (empty string is valid dict key)
        manager.add_mute("", timedelta(hours=1))
        assert manager.is_muted("")

    def test_special_characters_in_key(self, tmp_path):
        """Test handling of special characters in key."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        special_key = "container:with:colons/and/slashes"
        manager.add_mute(special_key, timedelta(hours=1))

        assert manager.is_muted(special_key)

        # Verify it persists
        manager2 = ConcreteMuteManager(json_path=str(json_file))
        assert manager2.is_muted(special_key)

    def test_zero_duration(self, tmp_path):
        """Test handling of zero duration (immediately expired)."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        manager.add_mute("plex", timedelta(seconds=0))

        # Should be immediately expired
        assert not manager.is_muted("plex")

    def test_very_long_duration(self, tmp_path):
        """Test handling of very long duration."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        manager.add_mute("plex", timedelta(days=365))

        assert manager.is_muted("plex")

    def test_concurrent_read_write_safety(self, tmp_path):
        """Test basic concurrent operation safety (not full thread safety test)."""
        json_file = tmp_path / "mutes.json"
        manager = ConcreteMuteManager(json_path=str(json_file))

        # Add multiple mutes rapidly
        for i in range(10):
            manager.add_mute(f"container{i}", timedelta(hours=1))

        mutes = manager.get_active_mutes()
        assert len(mutes) == 10

    def test_json_file_permissions_error(self, tmp_path):
        """Test handling of file write permission error."""
        import os

        json_file = tmp_path / "readonly_dir" / "mutes.json"
        json_file.parent.mkdir()

        # Create manager (directory is writable at this point)
        manager = ConcreteMuteManager(json_path=str(json_file))

        # Make directory read-only
        os.chmod(json_file.parent, 0o444)

        try:
            # This should log error but not crash
            manager.add_mute("plex", timedelta(hours=1))

            # Mute should still be in memory
            assert "plex" in manager._mutes
        finally:
            # Restore permissions for cleanup
            os.chmod(json_file.parent, 0o755)
