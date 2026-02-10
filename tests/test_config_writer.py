"""Tests for ConfigWriter."""

import pytest
import yaml
from pathlib import Path

from src.config import ConfigWriter


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path / "config.yaml")


class TestConfigWriter:
    def test_write_creates_config(self, config_path):
        writer = ConfigWriter(config_path)
        writer.write(
            unraid_host="192.168.0.190",
            unraid_port=80,
            unraid_use_ssl=False,
            watched_containers=["plex", "radarr", "sonarr"],
            protected_containers=["unraid-monitor-bot"],
            ignored_containers=["dozzle"],
            priority_containers=["mariadb", "redis"],
            killable_containers=["qbit"],
        )

        assert Path(config_path).exists()
        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert config["unraid"]["host"] == "192.168.0.190"
        assert config["unraid"]["enabled"] is True
        assert config["log_watching"]["containers"] == ["plex", "radarr", "sonarr"]
        assert config["protected_containers"] == ["unraid-monitor-bot"]
        assert config["ignored_containers"] == ["dozzle"]
        assert config["memory_management"]["priority_containers"] == ["mariadb", "redis"]
        assert config["memory_management"]["killable_containers"] == ["qbit"]

    def test_write_without_unraid(self, config_path):
        writer = ConfigWriter(config_path)
        writer.write(
            unraid_host=None,
            unraid_port=80,
            unraid_use_ssl=False,
            watched_containers=["plex"],
            protected_containers=["unraid-monitor-bot"],
            ignored_containers=[],
            priority_containers=[],
            killable_containers=[],
        )

        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert config["unraid"]["enabled"] is False

    def test_merge_preserves_thresholds(self, config_path):
        writer = ConfigWriter(config_path)
        writer.write(
            unraid_host="192.168.0.190",
            unraid_port=80,
            unraid_use_ssl=False,
            watched_containers=["plex"],
            protected_containers=["unraid-monitor-bot"],
            ignored_containers=[],
            priority_containers=[],
            killable_containers=[],
        )

        # Manually edit thresholds
        with open(config_path) as f:
            config = yaml.safe_load(f)
        config["unraid"]["thresholds"]["cpu_temp"] = 70
        config["resource_monitoring"]["defaults"]["cpu_percent"] = 90
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Merge with new container roles
        writer.merge(
            unraid_host="192.168.0.200",
            unraid_port=443,
            unraid_use_ssl=True,
            watched_containers=["plex", "radarr"],
            protected_containers=["unraid-monitor-bot"],
            ignored_containers=["kometa"],
            priority_containers=["mariadb"],
            killable_containers=["qbit"],
        )

        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Container roles updated
        assert config["log_watching"]["containers"] == ["plex", "radarr"]
        assert config["ignored_containers"] == ["kometa"]
        # Unraid connection updated
        assert config["unraid"]["host"] == "192.168.0.200"
        assert config["unraid"]["use_ssl"] is True
        # Thresholds preserved
        assert config["unraid"]["thresholds"]["cpu_temp"] == 70
        assert config["resource_monitoring"]["defaults"]["cpu_percent"] == 90

    def test_write_includes_default_sections(self, config_path):
        writer = ConfigWriter(config_path)
        writer.write(
            unraid_host=None,
            unraid_port=80,
            unraid_use_ssl=False,
            watched_containers=[],
            protected_containers=["unraid-monitor-bot"],
            ignored_containers=[],
            priority_containers=[],
            killable_containers=[],
        )

        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert "ai" in config
        assert "bot" in config
        assert "docker" in config
        assert "log_watching" in config
        assert "resource_monitoring" in config
        assert "memory_management" in config
        assert "unraid" in config
