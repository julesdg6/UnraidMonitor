"""Integration test for setup wizard flow."""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from src.bot.setup_wizard import SetupWizard, WizardState, format_classification_summary
from src.services.container_classifier import ContainerClassification


@pytest.mark.asyncio
async def test_full_wizard_flow(tmp_path):
    """Test complete wizard flow: start -> host -> classify -> confirm."""
    config_path = str(tmp_path / "config.yaml")

    mock_docker = MagicMock()
    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.image.tags = ["plexinc/plex-media-server:latest"]
    mock_container.status = "running"
    mock_docker.containers.list.return_value = [mock_container]

    wizard = SetupWizard(
        config_path=config_path,
        docker_client=mock_docker,
        anthropic_client=None,
        unraid_api_key="test-key",
    )

    user_id = 123

    # Step 1: Start
    wizard.start(user_id)
    assert wizard.get_state(user_id) == WizardState.AWAITING_HOST

    # Step 2: Set host + connection success
    wizard.set_host(user_id, "192.168.0.190")
    wizard.connection_result(user_id, success=True, port=80, use_ssl=False)
    assert wizard.get_state(user_id) == WizardState.REVIEW_CONTAINERS

    # Step 3: Classify containers
    classifications = await wizard.classify_containers(user_id)
    assert len(classifications) == 1
    assert "watched" in classifications[0].categories

    # Step 4: Confirm
    wizard.save_config(user_id, merge=False)
    wizard.confirm(user_id)
    assert wizard.get_state(user_id) == WizardState.COMPLETE

    # Verify config was saved
    assert Path(config_path).exists()
    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)

    assert config["unraid"]["host"] == "192.168.0.190"
    assert config["unraid"]["enabled"] is True
    assert "plex" in config["log_watching"]["containers"]


@pytest.mark.asyncio
async def test_wizard_flow_without_unraid(tmp_path):
    """Test wizard flow when no Unraid API key is set."""
    config_path = str(tmp_path / "config.yaml")

    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = []

    wizard = SetupWizard(
        config_path=config_path,
        docker_client=mock_docker,
        anthropic_client=None,
        unraid_api_key=None,
    )

    user_id = 123

    # Start should skip straight to review
    wizard.start(user_id)
    assert wizard.get_state(user_id) == WizardState.REVIEW_CONTAINERS

    # Classify (empty)
    classifications = await wizard.classify_containers(user_id)
    assert len(classifications) == 0

    # Confirm
    wizard.save_config(user_id, merge=False)
    wizard.confirm(user_id)

    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)

    assert config["unraid"]["enabled"] is False


def test_format_classification_summary_groups_correctly():
    """Summary should group containers by category with AI markers."""
    classifications = [
        ContainerClassification(name="mariadb", image="", categories={"priority", "watched"}),
        ContainerClassification(name="plex", image="", categories={"watched"}),
        ContainerClassification(
            name="bookstack", image="", categories={"watched"}, ai_suggested=True
        ),
        ContainerClassification(
            name="dozzle", image="", categories={"ignored"}, ai_suggested=True
        ),
        ContainerClassification(name="unknown-app", image="", categories=set()),
    ]

    summary = format_classification_summary(classifications)

    assert "mariadb" in summary
    assert "plex" in summary
    # AI-suggested containers get \* marker (escaped for Telegram Markdown)
    assert "bookstack \\*" in summary
    assert "dozzle \\*" in summary
    assert "unknown-app" in summary
    assert "AI-suggested" in summary
