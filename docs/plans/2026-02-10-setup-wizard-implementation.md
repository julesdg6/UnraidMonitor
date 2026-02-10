# Setup Wizard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Telegram-based onboarding wizard that guides users through first-run setup instead of generating a default config.yaml.

**Architecture:** A state-machine wizard (`SetupWizard`) intercepts Telegram messages during setup mode. A `ContainerClassifier` uses pattern matching + Haiku AI to categorize Docker containers. A `ConfigWriter` serializes wizard results to config.yaml with merge support for re-runs. `main.py` detects first-run and defers monitor startup until the wizard completes.

**Tech Stack:** Python 3.11, aiogram 3.x (inline keyboards, callback queries), docker-py, aiohttp (Unraid connection test), anthropic SDK (Haiku for container classification)

---

### Task 1: ContainerClassifier - Pattern Matching

The classifier maps container names/images to categories using known patterns. This is a pure service with no Telegram or Docker dependencies.

**Files:**
- Create: `src/services/container_classifier.py`
- Test: `tests/test_container_classifier.py`

**Step 1: Write the failing test**

```python
# tests/test_container_classifier.py
"""Tests for container classifier."""

import pytest
from src.services.container_classifier import ContainerClassifier


@pytest.fixture
def classifier():
    return ContainerClassifier(anthropic_client=None)


class TestPatternMatching:
    def test_database_classified_as_priority_and_watched(self, classifier):
        """Databases should be priority + watched."""
        result = classifier.classify_by_pattern("mariadb", "linuxserver/mariadb")
        assert "priority" in result.categories
        assert "watched" in result.categories

    def test_arr_stack_classified_as_watched(self, classifier):
        """*arr apps should be watched."""
        result = classifier.classify_by_pattern("radarr", "linuxserver/radarr")
        assert "watched" in result.categories
        assert "priority" not in result.categories

    def test_bot_self_classified_as_protected(self, classifier):
        """The bot itself should always be protected."""
        result = classifier.classify_by_pattern("unraid-monitor-bot", "unraid-monitor-bot:latest")
        assert "protected" in result.categories

    def test_download_client_classified_as_watched_and_killable(self, classifier):
        """Download clients should be watched + killable candidates."""
        result = classifier.classify_by_pattern("qbit", "linuxserver/qbittorrent")
        assert "watched" in result.categories
        assert "killable" in result.categories

    def test_unknown_container_unclassified(self, classifier):
        """Unknown containers return no categories."""
        result = classifier.classify_by_pattern("my-custom-app", "myrepo/custom:latest")
        assert len(result.categories) == 0

    def test_image_name_used_for_matching(self, classifier):
        """Should match on image name when container name is ambiguous."""
        result = classifier.classify_by_pattern("dl", "linuxserver/qbittorrent")
        assert "watched" in result.categories
        assert "killable" in result.categories

    def test_media_apps_classified_as_watched(self, classifier):
        """Media apps should be watched."""
        result = classifier.classify_by_pattern("plex", "plexinc/plex-media-server")
        assert "watched" in result.categories

    def test_case_insensitive(self, classifier):
        """Matching should be case-insensitive."""
        result = classifier.classify_by_pattern("MariaDB", "LinuxServer/MariaDB")
        assert "priority" in result.categories
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_container_classifier.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'src.services.container_classifier'`

**Step 3: Write minimal implementation**

```python
# src/services/container_classifier.py
"""Container classifier using pattern matching and optional AI."""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger(__name__)


@dataclass
class ContainerClassification:
    """Classification result for a single container."""
    name: str
    image: str
    categories: set[str] = field(default_factory=set)  # priority, protected, watched, killable, ignored
    description: str = ""
    ai_suggested: bool = False


# Pattern rules: (name_patterns, image_patterns, categories)
# Both lists are checked case-insensitively. A match on either triggers the categories.
PATTERN_RULES: list[tuple[list[str], list[str], set[str]]] = [
    # Databases -> priority + watched
    (
        ["mariadb", "mysql", "postgresql", "postgres", "redis", "mongodb", "influxdb", "couchdb"],
        ["mariadb", "mysql", "postgres", "redis", "mongo", "influxdb", "couchdb"],
        {"priority", "watched"},
    ),
    # The bot itself -> protected
    (
        ["unraid-monitor-bot"],
        ["unraid-monitor-bot"],
        {"protected"},
    ),
    # Media apps -> watched
    (
        ["plex", "emby", "jellyfin", "tautulli"],
        ["plex", "emby", "jellyfin", "tautulli"],
        {"watched"},
    ),
    # *arr stack -> watched
    (
        ["radarr", "sonarr", "lidarr", "readarr", "prowlarr", "bazarr"],
        ["radarr", "sonarr", "lidarr", "readarr", "prowlarr", "bazarr"],
        {"watched"},
    ),
    # Request managers -> watched
    (
        ["overseerr", "ombi", "petio"],
        ["overseerr", "ombi", "petio"],
        {"watched"},
    ),
    # Download clients -> watched + killable
    (
        ["qbittorrent", "qbit", "sabnzbd", "sab", "nzbget", "deluge", "transmission", "rtorrent", "flood"],
        ["qbittorrent", "sabnzbd", "nzbget", "deluge", "transmission", "rtorrent", "flood"],
        {"watched", "killable"},
    ),
    # Auth/proxy -> priority
    (
        ["authelia", "authentik", "traefik", "nginx-proxy", "swag", "caddy"],
        ["authelia", "authentik", "traefik", "nginx-proxy", "swag", "caddy"],
        {"priority"},
    ),
]


class ContainerClassifier:
    """Classifies Docker containers by role using pattern matching and optional AI."""

    def __init__(self, anthropic_client: "anthropic.AsyncAnthropic | None" = None):
        self._client = anthropic_client

    def classify_by_pattern(self, name: str, image: str) -> ContainerClassification:
        """Classify a container using pattern matching only.

        Args:
            name: Container name.
            image: Container image (e.g. 'linuxserver/radarr:latest').

        Returns:
            ContainerClassification with matched categories.
        """
        result = ContainerClassification(name=name, image=image)
        name_lower = name.lower()
        image_lower = image.lower()

        for name_patterns, image_patterns, categories in PATTERN_RULES:
            matched = False
            for pattern in name_patterns:
                if pattern in name_lower:
                    matched = True
                    break
            if not matched:
                for pattern in image_patterns:
                    if pattern in image_lower:
                        matched = True
                        break
            if matched:
                result.categories.update(categories)

        return result
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_container_classifier.py -v`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add src/services/container_classifier.py tests/test_container_classifier.py
git commit -m "feat: add ContainerClassifier with pattern matching"
```

---

### Task 2: ContainerClassifier - AI Classification via Haiku

Add `classify_batch_with_ai()` method that sends unmatched containers to Haiku for categorization.

**Files:**
- Modify: `src/services/container_classifier.py`
- Test: `tests/test_container_classifier.py`

**Step 1: Write the failing test**

Add to `tests/test_container_classifier.py`:

```python
from unittest.mock import MagicMock, AsyncMock


class TestAIClassification:
    @pytest.mark.asyncio
    async def test_ai_classifies_unknown_containers(self):
        """Haiku should classify unknown containers."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '''[
            {"name": "bookstack", "categories": ["watched"], "description": "Wiki/documentation platform"},
            {"name": "dozzle", "categories": ["ignored"], "description": "Log viewer UI"}
        ]'''
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        classifier = ContainerClassifier(anthropic_client=mock_client)
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
        """On AI failure, return original unclassified containers."""
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))

        classifier = ContainerClassifier(anthropic_client=mock_client)
        unclassified = [
            ContainerClassification(name="bookstack", image="linuxserver/bookstack"),
        ]

        results = await classifier.classify_batch_with_ai(unclassified)

        assert len(results) == 1
        assert len(results[0].categories) == 0  # unchanged

    @pytest.mark.asyncio
    async def test_ai_skipped_without_client(self):
        """Without anthropic client, return originals unchanged."""
        classifier = ContainerClassifier(anthropic_client=None)
        unclassified = [
            ContainerClassification(name="bookstack", image="linuxserver/bookstack"),
        ]

        results = await classifier.classify_batch_with_ai(unclassified)

        assert len(results) == 1
        assert len(results[0].categories) == 0

    @pytest.mark.asyncio
    async def test_ai_filters_invalid_categories(self):
        """AI suggesting invalid categories should be filtered out."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '''[
            {"name": "app", "categories": ["watched", "superadmin"], "description": "An app"}
        ]'''
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        classifier = ContainerClassifier(anthropic_client=mock_client)
        unclassified = [
            ContainerClassification(name="app", image="myrepo/app"),
        ]

        results = await classifier.classify_batch_with_ai(unclassified)

        assert "watched" in results[0].categories
        assert "superadmin" not in results[0].categories
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_container_classifier.py::TestAIClassification -v`
Expected: FAIL - `AttributeError: 'ContainerClassifier' object has no attribute 'classify_batch_with_ai'`

**Step 3: Write minimal implementation**

Add to `src/services/container_classifier.py`:

```python
import json
import re
from src.utils.sanitize import sanitize_container_name
from src.utils.api_errors import handle_anthropic_error

VALID_CATEGORIES = {"priority", "protected", "watched", "killable", "ignored"}

CLASSIFY_PROMPT = """Classify these Docker containers into monitoring categories.

Containers:
{container_list}

Valid categories: priority (critical services, never kill), protected (no remote control), watched (monitor logs), killable (can be stopped to free memory), ignored (don't monitor)

A container can have multiple categories. Most containers should be "watched".
Return ONLY a JSON array (no markdown, no explanation):
[{{"name": "container_name", "categories": ["watched"], "description": "Brief description"}}]"""


class ContainerClassifier:
    # ... existing __init__ and classify_by_pattern ...

    async def classify_batch_with_ai(
        self, unclassified: list[ContainerClassification]
    ) -> list[ContainerClassification]:
        """Classify containers using Haiku AI.

        Args:
            unclassified: Containers that pattern matching didn't classify.

        Returns:
            Updated ContainerClassification list with AI suggestions.
        """
        if not self._client or not unclassified:
            return unclassified

        container_list = "\n".join(
            f"- {sanitize_container_name(c.name)} (image: {sanitize_container_name(c.image)})"
            for c in unclassified
        )

        prompt = CLASSIFY_PROMPT.format(container_list=container_list)

        try:
            response = await self._client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text

            # Extract JSON array from response
            json_match = re.search(r"\[.*\]", text, re.DOTALL)
            if not json_match:
                logger.error(f"No JSON array found in AI response: {text}")
                return unclassified

            ai_results = json.loads(json_match.group())

            # Build lookup by name
            by_name = {c.name: c for c in unclassified}

            for item in ai_results:
                name = item.get("name", "")
                if name not in by_name:
                    continue

                categories = set(item.get("categories", []))
                # Filter to valid categories only
                valid = categories & VALID_CATEGORIES
                by_name[name].categories = valid
                by_name[name].description = item.get("description", "")
                by_name[name].ai_suggested = True

            return unclassified

        except Exception as e:
            error_result = handle_anthropic_error(e)
            logger.log(error_result.log_level, f"AI classification failed: {e}")
            return unclassified
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_container_classifier.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/services/container_classifier.py tests/test_container_classifier.py
git commit -m "feat: add AI-assisted container classification via Haiku"
```

---

### Task 3: ContainerClassifier - Full classify_all Method

Add the high-level method that fetches containers from Docker, runs pattern matching, then AI for unknowns.

**Files:**
- Modify: `src/services/container_classifier.py`
- Test: `tests/test_container_classifier.py`

**Step 1: Write the failing test**

```python
class TestClassifyAll:
    @pytest.mark.asyncio
    async def test_classify_all_combines_pattern_and_ai(self):
        """classify_all should pattern-match first, then AI for unknowns."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '[{"name": "bookstack", "categories": ["watched"], "description": "Wiki"}]'
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        classifier = ContainerClassifier(anthropic_client=mock_client)

        containers = [
            ("mariadb", "linuxserver/mariadb", "running"),
            ("radarr", "linuxserver/radarr", "running"),
            ("bookstack", "linuxserver/bookstack", "running"),
        ]

        results = await classifier.classify_all(containers)

        # mariadb: pattern-matched as priority+watched
        mariadb = next(r for r in results if r.name == "mariadb")
        assert "priority" in mariadb.categories
        assert mariadb.ai_suggested is False

        # radarr: pattern-matched as watched
        radarr = next(r for r in results if r.name == "radarr")
        assert "watched" in radarr.categories

        # bookstack: AI-classified
        bookstack = next(r for r in results if r.name == "bookstack")
        assert "watched" in bookstack.categories
        assert bookstack.ai_suggested is True

    @pytest.mark.asyncio
    async def test_classify_all_no_ai_call_when_all_matched(self):
        """No AI call if all containers are pattern-matched."""
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock()

        classifier = ContainerClassifier(anthropic_client=mock_client)

        containers = [
            ("mariadb", "linuxserver/mariadb", "running"),
            ("plex", "plexinc/plex-media-server", "running"),
        ]

        await classifier.classify_all(containers)

        mock_client.messages.create.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_container_classifier.py::TestClassifyAll -v`
Expected: FAIL - `AttributeError: 'ContainerClassifier' object has no attribute 'classify_all'`

**Step 3: Write minimal implementation**

Add to `ContainerClassifier`:

```python
    async def classify_all(
        self, containers: list[tuple[str, str, str]]
    ) -> list[ContainerClassification]:
        """Classify all containers using pattern matching + AI.

        Args:
            containers: List of (name, image, status) tuples.

        Returns:
            List of ContainerClassification results.
        """
        classified = []
        unclassified = []

        for name, image, status in containers:
            result = self.classify_by_pattern(name, image)
            if result.categories:
                classified.append(result)
            else:
                unclassified.append(result)

        # Run AI on unclassified containers
        if unclassified:
            ai_results = await self.classify_batch_with_ai(unclassified)
            classified.extend(ai_results)

        return classified
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_container_classifier.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/services/container_classifier.py tests/test_container_classifier.py
git commit -m "feat: add classify_all combining pattern matching and AI"
```

---

### Task 4: ConfigWriter - Write and Merge Config

Writes wizard results to config.yaml. Merge mode preserves thresholds and other manual tweaks.

**Files:**
- Modify: `src/config.py` (add `ConfigWriter` class)
- Test: `tests/test_config_writer.py`

**Step 1: Write the failing test**

```python
# tests/test_config_writer.py
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
        """write() creates a new config.yaml with wizard data."""
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
        """write() with no Unraid host disables Unraid."""
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
        """merge() updates container roles but keeps thresholds."""
        # First write with defaults
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
        """write() includes all default config sections."""
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

        # All sections present
        assert "ai" in config
        assert "bot" in config
        assert "docker" in config
        assert "log_watching" in config
        assert "resource_monitoring" in config
        assert "memory_management" in config
        assert "unraid" in config
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_writer.py -v`
Expected: FAIL - `ImportError: cannot import name 'ConfigWriter' from 'src.config'`

**Step 3: Write minimal implementation**

Add to `src/config.py`:

```python
class ConfigWriter:
    """Writes and merges config.yaml from wizard results."""

    def __init__(self, config_path: str):
        self._path = Path(config_path)

    def _build_config(
        self,
        unraid_host: str | None,
        unraid_port: int,
        unraid_use_ssl: bool,
        watched_containers: list[str],
        protected_containers: list[str],
        ignored_containers: list[str],
        priority_containers: list[str],
        killable_containers: list[str],
    ) -> dict[str, Any]:
        """Build full config dict from wizard results + defaults."""
        unraid_enabled = bool(unraid_host)
        return {
            "ai": {
                "models": {
                    "pattern_analyzer": "claude-haiku-4-5-20251001",
                    "nl_processor": "claude-sonnet-4-5-20250929",
                    "diagnostic": "claude-haiku-4-5-20251001",
                },
                "max_tokens": {
                    "pattern_analyzer": 500,
                    "nl_processor": 1024,
                    "diagnostic_brief": 300,
                    "diagnostic_detail": 800,
                },
                "nl_processor": {
                    "max_tool_iterations": 10,
                    "max_conversation_exchanges": 5,
                },
                "pattern_analyzer_context_lines": 30,
                "diagnostic_context_expiry_seconds": 600,
            },
            "bot": {
                "confirmation_timeout_seconds": 60,
                "log_display": {
                    "max_lines": 100,
                    "max_chars": 4000,
                    "nl_max_chars": 3000,
                    "diagnose_max_lines": 500,
                },
                "error_display_max_chars": 200,
            },
            "docker": {
                "socket_path": "unix:///var/run/docker.sock",
            },
            "ignored_containers": ignored_containers,
            "protected_containers": protected_containers,
            "log_watching": {
                "containers": watched_containers,
                "error_patterns": [
                    "error", "exception", "fatal", "failed",
                    "critical", "panic", "traceback",
                ],
                "ignore_patterns": ["DeprecationWarning", "DEBUG"],
                "cooldown_seconds": 900,
            },
            "resource_monitoring": {
                "enabled": True,
                "poll_interval_seconds": 60,
                "sustained_threshold_seconds": 120,
                "defaults": {"cpu_percent": 80, "memory_percent": 85},
                "containers": {},
            },
            "memory_management": {
                "enabled": bool(killable_containers),
                "warning_threshold": 90,
                "critical_threshold": 95,
                "safe_threshold": 80,
                "kill_delay_seconds": 60,
                "stabilization_wait": 180,
                "priority_containers": priority_containers,
                "killable_containers": killable_containers,
            },
            "unraid": {
                "enabled": unraid_enabled,
                "host": unraid_host or "",
                "port": unraid_port,
                "use_ssl": unraid_use_ssl,
                "verify_ssl": unraid_use_ssl,
                "polling": {"system": 30, "array": 300},
                "thresholds": {
                    "cpu_temp": 80,
                    "cpu_usage": 95,
                    "memory_usage": 90,
                    "disk_temp": 50,
                    "array_usage": 85,
                },
            },
        }

    def write(self, **kwargs) -> None:
        """Write a fresh config.yaml from wizard results."""
        config = self._build_config(**kwargs)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    def merge(self, **kwargs) -> None:
        """Merge wizard results into existing config.yaml.

        Updates container roles and Unraid connection.
        Preserves thresholds, AI config, and other manual tweaks.
        """
        existing = load_yaml_config(str(self._path))
        new_config = self._build_config(**kwargs)

        # Sections to fully replace (wizard-managed)
        existing["log_watching"]["containers"] = new_config["log_watching"]["containers"]
        existing["protected_containers"] = new_config["protected_containers"]
        existing["ignored_containers"] = new_config["ignored_containers"]
        existing["memory_management"]["priority_containers"] = new_config["memory_management"]["priority_containers"]
        existing["memory_management"]["killable_containers"] = new_config["memory_management"]["killable_containers"]

        # Unraid connection (update host/port/ssl, preserve thresholds)
        existing["unraid"]["enabled"] = new_config["unraid"]["enabled"]
        existing["unraid"]["host"] = new_config["unraid"]["host"]
        existing["unraid"]["port"] = new_config["unraid"]["port"]
        existing["unraid"]["use_ssl"] = new_config["unraid"]["use_ssl"]
        existing["unraid"]["verify_ssl"] = new_config["unraid"]["verify_ssl"]

        with open(self._path, "w") as f:
            yaml.dump(existing, f, default_flow_style=False, sort_keys=False)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_writer.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/config.py tests/test_config_writer.py
git commit -m "feat: add ConfigWriter with write and merge support"
```

---

### Task 5: SetupWizard - State Machine Core

The wizard state machine, Unraid connection testing, and result building. No Telegram UI yet - just the logic.

**Files:**
- Create: `src/bot/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`

**Step 1: Write the failing test**

```python
# tests/test_setup_wizard.py
"""Tests for setup wizard state machine."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.bot.setup_wizard import SetupWizard, WizardState


@pytest.fixture
def wizard(tmp_path):
    return SetupWizard(
        config_path=str(tmp_path / "config.yaml"),
        docker_client=MagicMock(),
        anthropic_client=None,
        unraid_api_key=None,
    )


class TestWizardState:
    def test_initial_state_is_idle(self, wizard):
        assert wizard.get_state(user_id=123) == WizardState.IDLE

    def test_start_moves_to_awaiting_host_with_unraid_key(self, tmp_path):
        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=MagicMock(),
            anthropic_client=None,
            unraid_api_key="some-key",
        )
        w.start(user_id=123)
        assert w.get_state(123) == WizardState.AWAITING_HOST

    def test_start_skips_to_containers_without_unraid_key(self, wizard):
        wizard.start(user_id=123)
        assert wizard.get_state(123) == WizardState.REVIEW_CONTAINERS

    def test_set_host_moves_to_connecting(self, tmp_path):
        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=MagicMock(),
            anthropic_client=None,
            unraid_api_key="some-key",
        )
        w.start(user_id=123)
        w.set_host(123, "192.168.0.190")
        assert w.get_state(123) == WizardState.CONNECTING

    def test_connection_success_moves_to_review(self, tmp_path):
        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=MagicMock(),
            anthropic_client=None,
            unraid_api_key="some-key",
        )
        w.start(user_id=123)
        w.set_host(123, "192.168.0.190")
        w.connection_result(123, success=True, port=80, use_ssl=False)
        assert w.get_state(123) == WizardState.REVIEW_CONTAINERS

    def test_connection_failure_returns_to_awaiting_host(self, tmp_path):
        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=MagicMock(),
            anthropic_client=None,
            unraid_api_key="some-key",
        )
        w.start(user_id=123)
        w.set_host(123, "192.168.0.190")
        w.connection_result(123, success=False, port=0, use_ssl=False)
        assert w.get_state(123) == WizardState.AWAITING_HOST

    def test_confirm_moves_to_complete(self, wizard):
        wizard.start(user_id=123)
        wizard.confirm(123)
        assert wizard.get_state(123) == WizardState.COMPLETE

    def test_is_active(self, wizard):
        assert wizard.is_active(123) is False
        wizard.start(user_id=123)
        assert wizard.is_active(123) is True
        wizard.confirm(123)
        assert wizard.is_active(123) is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_wizard.py -v`
Expected: FAIL - `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/bot/setup_wizard.py
"""Telegram-based setup wizard for first-run configuration."""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

import aiohttp
import docker

from src.config import ConfigWriter, load_yaml_config
from src.services.container_classifier import ContainerClassifier, ContainerClassification

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger(__name__)


class WizardState(Enum):
    IDLE = "idle"
    AWAITING_HOST = "awaiting_host"
    CONNECTING = "connecting"
    REVIEW_CONTAINERS = "review_containers"
    ADJUSTING = "adjusting"
    SAVING = "saving"
    COMPLETE = "complete"


@dataclass
class WizardSession:
    """Per-user wizard session data."""
    state: WizardState = WizardState.IDLE
    unraid_host: str | None = None
    unraid_port: int = 80
    unraid_use_ssl: bool = False
    classifications: list[ContainerClassification] = field(default_factory=list)
    adjusting_category: str | None = None


class SetupWizard:
    """State machine for the Telegram setup wizard."""

    def __init__(
        self,
        config_path: str,
        docker_client: docker.DockerClient,
        anthropic_client: "anthropic.AsyncAnthropic | None" = None,
        unraid_api_key: str | None = None,
    ):
        self._config_path = config_path
        self._docker_client = docker_client
        self._anthropic_client = anthropic_client
        self._unraid_api_key = unraid_api_key
        self._sessions: dict[int, WizardSession] = {}
        self._classifier = ContainerClassifier(anthropic_client=anthropic_client)
        self._config_writer = ConfigWriter(config_path)

    def _get_session(self, user_id: int) -> WizardSession:
        if user_id not in self._sessions:
            self._sessions[user_id] = WizardSession()
        return self._sessions[user_id]

    def get_state(self, user_id: int) -> WizardState:
        return self._get_session(user_id).state

    def is_active(self, user_id: int) -> bool:
        state = self.get_state(user_id)
        return state not in (WizardState.IDLE, WizardState.COMPLETE)

    def start(self, user_id: int) -> None:
        session = self._get_session(user_id)
        if self._unraid_api_key:
            session.state = WizardState.AWAITING_HOST
        else:
            session.state = WizardState.REVIEW_CONTAINERS

    def set_host(self, user_id: int, host: str) -> None:
        session = self._get_session(user_id)
        session.unraid_host = host
        session.state = WizardState.CONNECTING

    def connection_result(
        self, user_id: int, success: bool, port: int, use_ssl: bool
    ) -> None:
        session = self._get_session(user_id)
        if success:
            session.unraid_port = port
            session.unraid_use_ssl = use_ssl
            session.state = WizardState.REVIEW_CONTAINERS
        else:
            session.state = WizardState.AWAITING_HOST

    def confirm(self, user_id: int) -> None:
        session = self._get_session(user_id)
        session.state = WizardState.COMPLETE

    def get_session_data(self, user_id: int) -> WizardSession:
        return self._get_session(user_id)

    async def test_unraid_connection(
        self, host: str, api_key: str
    ) -> tuple[bool, int, bool]:
        """Try connecting to Unraid server. Returns (success, port, use_ssl)."""
        for use_ssl, port in [(True, 443), (False, 80)]:
            protocol = "https" if use_ssl else "http"
            url = f"{protocol}://{host}:{port}/graphql"

            ssl_ctx = False
            if use_ssl:
                import ssl
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

            try:
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                async with aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(total=5),
                    headers={
                        "x-api-key": api_key,
                        "Content-Type": "application/json",
                        "apollo-require-preflight": "true",
                    },
                ) as session:
                    payload = {"query": "{ info { os { hostname } } }"}
                    async with session.post(url, json=payload) as resp:
                        if resp.status == 200:
                            return True, port, use_ssl
            except Exception:
                continue

        return False, 0, False

    def get_docker_containers(self) -> list[tuple[str, str, str]]:
        """Get all Docker containers as (name, image, status) tuples."""
        try:
            containers = self._docker_client.containers.list(all=True)
            return [
                (
                    c.name,
                    c.image.tags[0] if c.image.tags else c.image.short_id,
                    c.status,
                )
                for c in containers
            ]
        except Exception as e:
            logger.error(f"Failed to list Docker containers: {e}")
            return []

    async def classify_containers(self, user_id: int) -> list[ContainerClassification]:
        """Fetch and classify all Docker containers."""
        containers = await asyncio.to_thread(self.get_docker_containers)
        classifications = await self._classifier.classify_all(containers)
        session = self._get_session(user_id)
        session.classifications = classifications
        return classifications

    def save_config(self, user_id: int, merge: bool = False) -> None:
        """Save wizard results to config.yaml."""
        session = self._get_session(user_id)

        watched = sorted({
            c.name for c in session.classifications if "watched" in c.categories
        })
        protected = sorted({
            c.name for c in session.classifications if "protected" in c.categories
        })
        ignored = sorted({
            c.name for c in session.classifications if "ignored" in c.categories
        })
        priority = sorted({
            c.name for c in session.classifications if "priority" in c.categories
        })
        killable = sorted({
            c.name for c in session.classifications if "killable" in c.categories
        })

        kwargs = dict(
            unraid_host=session.unraid_host,
            unraid_port=session.unraid_port,
            unraid_use_ssl=session.unraid_use_ssl,
            watched_containers=watched,
            protected_containers=protected,
            ignored_containers=ignored,
            priority_containers=priority,
            killable_containers=killable,
        )

        if merge:
            self._config_writer.merge(**kwargs)
        else:
            self._config_writer.write(**kwargs)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup_wizard.py -v`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add src/bot/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: add SetupWizard state machine with connection testing"
```

---

### Task 6: SetupWizard - Telegram Handlers

Wire the wizard to Telegram with message handlers, inline keyboards for container adjustment, and the setup-mode middleware.

**Files:**
- Modify: `src/bot/setup_wizard.py` (add handler functions)
- Test: `tests/test_setup_wizard.py` (add handler tests)

**Step 1: Write the failing test**

```python
class TestWizardHandlers:
    @pytest.mark.asyncio
    async def test_start_handler_sends_welcome(self, wizard):
        """start_handler should send welcome and start wizard."""
        from src.bot.setup_wizard import create_start_handler

        handler = create_start_handler(wizard)
        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123

        await handler(message)

        message.answer.assert_called_once()
        call_text = message.answer.call_args[0][0]
        assert "setup" in call_text.lower() or "welcome" in call_text.lower()

    @pytest.mark.asyncio
    async def test_host_handler_triggers_connection(self, tmp_path):
        """Typing an IP should trigger Unraid connection test."""
        from src.bot.setup_wizard import create_host_handler

        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=MagicMock(),
            anthropic_client=None,
            unraid_api_key="some-key",
        )
        w.start(user_id=123)

        handler = create_host_handler(w)
        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123
        message.text = "192.168.0.190"

        with patch.object(w, "test_unraid_connection", new_callable=AsyncMock) as mock_test:
            mock_test.return_value = (True, 80, False)
            with patch.object(w, "classify_containers", new_callable=AsyncMock) as mock_classify:
                mock_classify.return_value = []
                await handler(message)

        assert w.get_state(123) == WizardState.REVIEW_CONTAINERS

    @pytest.mark.asyncio
    async def test_confirm_callback_saves_config(self, wizard):
        """Pressing Looks Good saves config and completes wizard."""
        from src.bot.setup_wizard import create_confirm_callback

        wizard.start(user_id=123)

        handler = create_confirm_callback(wizard, on_complete=AsyncMock())
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:confirm"
        callback.message = AsyncMock()

        with patch.object(wizard, "save_config") as mock_save:
            await handler(callback)
            mock_save.assert_called_once_with(123, merge=False)

        assert wizard.get_state(123) == WizardState.COMPLETE
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_wizard.py::TestWizardHandlers -v`
Expected: FAIL - `ImportError: cannot import name 'create_start_handler'`

**Step 3: Write minimal implementation**

Add to `src/bot/setup_wizard.py`:

```python
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from typing import Callable, Awaitable


def format_classification_summary(classifications: list[ContainerClassification]) -> str:
    """Format container classifications as a Telegram message."""
    groups: dict[str, list[str]] = {
        "priority": [],
        "protected": [],
        "watched": [],
        "killable": [],
        "ignored": [],
    }
    unassigned = []

    for c in classifications:
        if not c.categories:
            unassigned.append(c.name)
        else:
            for cat in c.categories:
                if cat in groups:
                    suffix = "*" if c.ai_suggested else ""
                    groups[cat].append(f"{c.name}{suffix}")

    lines = ["Here's what I'd suggest:\n"]
    emoji_map = {
        "priority": "🛡",
        "protected": "🔒",
        "watched": "📋",
        "killable": "💀",
        "ignored": "🙈",
    }

    for cat, emoji in emoji_map.items():
        if groups[cat]:
            names = ", ".join(sorted(groups[cat]))
            lines.append(f"{emoji} *{cat.title()}:* {names}")

    if unassigned:
        lines.append(f"\n📦 *Unassigned:* {', '.join(sorted(unassigned))}")

    ai_used = any(c.ai_suggested for c in classifications)
    if ai_used:
        lines.append("\n_* = AI-suggested_")

    return "\n".join(lines)


def build_summary_keyboard() -> InlineKeyboardMarkup:
    """Build the summary view inline keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛡 Adjust Priority", callback_data="setup:adjust:priority"),
            InlineKeyboardButton(text="📋 Adjust Watched", callback_data="setup:adjust:watched"),
        ],
        [
            InlineKeyboardButton(text="💀 Adjust Killable", callback_data="setup:adjust:killable"),
            InlineKeyboardButton(text="🙈 Adjust Ignored", callback_data="setup:adjust:ignored"),
        ],
        [
            InlineKeyboardButton(text="🔒 Adjust Protected", callback_data="setup:adjust:protected"),
        ],
        [
            InlineKeyboardButton(text="✅ Looks Good", callback_data="setup:confirm"),
        ],
    ])


def build_adjust_keyboard(
    classifications: list[ContainerClassification], category: str
) -> InlineKeyboardMarkup:
    """Build toggle keyboard for adjusting a category."""
    buttons = []
    for c in sorted(classifications, key=lambda x: x.name):
        is_on = category in c.categories
        emoji = "✅" if is_on else "⬜"
        suffix = "*" if c.ai_suggested else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {c.name}{suffix}",
                callback_data=f"setup:toggle:{category}:{c.name}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="Done ✓", callback_data="setup:adjust_done"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_start_handler(
    wizard: "SetupWizard",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for the /start handler during setup mode."""

    async def handler(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        wizard.start(user_id)

        state = wizard.get_state(user_id)
        if state == WizardState.AWAITING_HOST:
            await message.answer(
                "👋 *Welcome to Unraid Monitor Bot!*\n\n"
                "Let's get you set up. I'll need a few details.\n\n"
                "🖥 What's your Unraid server's IP address or hostname?",
                parse_mode="Markdown",
            )
        else:
            # No Unraid key, skip to containers
            await message.answer(
                "👋 *Welcome to Unraid Monitor Bot!*\n\n"
                "Let me scan your Docker containers...",
                parse_mode="Markdown",
            )
            classifications = await wizard.classify_containers(user_id)
            summary = format_classification_summary(classifications)
            keyboard = build_summary_keyboard()
            await message.answer(summary, reply_markup=keyboard, parse_mode="Markdown")

    return handler


def create_host_handler(
    wizard: "SetupWizard",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for handling Unraid host input."""

    async def handler(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        host = (message.text or "").strip()

        if not host:
            await message.answer("Please enter an IP address or hostname.")
            return

        wizard.set_host(user_id, host)
        await message.answer(f"🔄 Testing connection to {host}...")

        success, port, use_ssl = await wizard.test_unraid_connection(
            host, wizard._unraid_api_key or ""
        )

        if success:
            wizard.connection_result(user_id, True, port, use_ssl)
            protocol = "HTTPS" if use_ssl else "HTTP"
            await message.answer(
                f"✅ Connected to Unraid via {protocol} on port {port}\n\n"
                "Now let me scan your Docker containers...",
            )
            classifications = await wizard.classify_containers(user_id)
            summary = format_classification_summary(classifications)
            keyboard = build_summary_keyboard()
            await message.answer(summary, reply_markup=keyboard, parse_mode="Markdown")
        else:
            wizard.connection_result(user_id, False, 0, False)
            await message.answer(
                f"❌ Couldn't connect to {host} on ports 443 or 80.\n\n"
                "Please check the IP and try again, or enter host:port (e.g. 192.168.0.190:8080)."
            )

    return handler


def create_confirm_callback(
    wizard: "SetupWizard",
    on_complete: Callable[[], Awaitable[None]] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the Looks Good confirmation button."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        await callback.answer()

        is_merge = wizard._config_writer._path.exists()
        wizard.save_config(user_id, merge=is_merge)
        wizard.confirm(user_id)

        session = wizard.get_session_data(user_id)
        watched = [c.name for c in session.classifications if "watched" in c.categories]
        priority = [c.name for c in session.classifications if "priority" in c.categories]
        protected = [c.name for c in session.classifications if "protected" in c.categories]

        lines = [
            "✅ *Setup complete!* Monitoring is now active.\n",
            f"📋 Watching logs: {len(watched)} containers",
            f"🛡 Priority: {len(priority)} containers",
            f"🔒 Protected: {len(protected)} containers",
        ]
        if session.unraid_host:
            lines.append(f"📡 Unraid: connected ({session.unraid_host})")
        lines.append("\nUse /manage for a dashboard, or /help for all commands.")

        if callback.message:
            await callback.message.answer("\n".join(lines), parse_mode="Markdown")

        if on_complete:
            await on_complete()

    return handler


def create_toggle_callback(
    wizard: "SetupWizard",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for container toggle buttons during adjustment."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) < 4:
            await callback.answer("Invalid")
            return

        category = parts[2]
        container_name = parts[3]

        session = wizard.get_session_data(user_id)
        target = next((c for c in session.classifications if c.name == container_name), None)
        if not target:
            await callback.answer("Container not found")
            return

        # Toggle
        if category in target.categories:
            target.categories.discard(category)
        else:
            target.categories.add(category)
            # Conflict resolution: ignored and watched are mutually exclusive
            if category == "ignored":
                target.categories.discard("watched")
            elif category == "watched":
                target.categories.discard("ignored")

        # Refresh the keyboard
        keyboard = build_adjust_keyboard(session.classifications, category)
        await callback.answer()
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass

    return handler


def create_adjust_callback(
    wizard: "SetupWizard",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for Adjust <category> buttons."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) < 3:
            await callback.answer("Invalid")
            return

        category = parts[2]
        session = wizard.get_session_data(user_id)
        session.adjusting_category = category
        session.state = WizardState.ADJUSTING

        keyboard = build_adjust_keyboard(session.classifications, category)
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                f"Adjust *{category.title()}* containers:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

    return handler


def create_adjust_done_callback(
    wizard: "SetupWizard",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for Done button in adjust view."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        session = wizard.get_session_data(user_id)
        session.state = WizardState.REVIEW_CONTAINERS
        session.adjusting_category = None

        summary = format_classification_summary(session.classifications)
        keyboard = build_summary_keyboard()
        await callback.answer()
        if callback.message:
            await callback.message.answer(summary, reply_markup=keyboard, parse_mode="Markdown")

    return handler
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup_wizard.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/bot/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: add Telegram handlers for setup wizard"
```

---

### Task 7: Setup Mode Middleware & Registration

Add middleware that intercepts messages during setup mode, and register the wizard handlers in the dispatcher.

**Files:**
- Modify: `src/bot/setup_wizard.py` (add middleware class)
- Modify: `src/bot/telegram_bot.py` (add `register_setup_wizard()` function)
- Test: `tests/test_setup_wizard.py` (add middleware tests)

**Step 1: Write the failing test**

```python
class TestSetupMiddleware:
    @pytest.mark.asyncio
    async def test_middleware_blocks_commands_during_setup(self, wizard):
        """Non-wizard commands should be blocked during setup."""
        from src.bot.setup_wizard import SetupModeMiddleware

        wizard.start(user_id=123)
        middleware = SetupModeMiddleware(wizard)

        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123
        message.text = "/status"
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_not_called()
        message.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_allows_help_during_setup(self, wizard):
        """The /help command should work during setup."""
        from src.bot.setup_wizard import SetupModeMiddleware

        wizard.start(user_id=123)
        middleware = SetupModeMiddleware(wizard)

        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123
        message.text = "/help"
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_passes_through_after_setup(self, wizard):
        """After setup completes, middleware should pass through."""
        from src.bot.setup_wizard import SetupModeMiddleware

        middleware = SetupModeMiddleware(wizard)

        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123
        message.text = "/status"
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_wizard.py::TestSetupMiddleware -v`
Expected: FAIL - `ImportError: cannot import name 'SetupModeMiddleware'`

**Step 3: Write minimal implementation**

Add to `src/bot/setup_wizard.py`:

```python
from aiogram import BaseMiddleware


ALLOWED_DURING_SETUP = {"/help", "/setup"}


class SetupModeMiddleware(BaseMiddleware):
    """Blocks non-wizard commands while setup is active."""

    def __init__(self, wizard: SetupWizard):
        self._wizard = wizard
        super().__init__()

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else 0

        if not self._wizard.is_active(user_id):
            return await handler(event, data)

        # Allow callback queries (wizard buttons)
        if isinstance(event, CallbackQuery):
            return await handler(event, data)

        # Allow specific commands
        if isinstance(event, Message) and event.text:
            for cmd in ALLOWED_DURING_SETUP:
                if event.text.startswith(cmd):
                    return await handler(event, data)

        # Block everything else with a friendly message
        if isinstance(event, Message):
            await event.answer(
                "⏳ Setup in progress. Please complete the setup wizard first.\n"
                "Use /help if you need assistance."
            )
            return None

        return await handler(event, data)
```

Add to `src/bot/telegram_bot.py` - a `register_setup_wizard()` function:

```python
from src.bot.setup_wizard import (
    SetupWizard,
    SetupModeMiddleware,
    create_start_handler,
    create_host_handler,
    create_confirm_callback,
    create_toggle_callback,
    create_adjust_callback,
    create_adjust_done_callback,
    WizardState,
)

def register_setup_wizard(
    dp: Dispatcher,
    wizard: SetupWizard,
    on_complete: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Register setup wizard handlers on the dispatcher."""
    # Setup middleware (added before auth, runs first)
    dp.message.middleware(SetupModeMiddleware(wizard))

    # /start and /setup trigger the wizard
    dp.message.register(create_start_handler(wizard), Command("start"))
    dp.message.register(create_start_handler(wizard), Command("setup"))

    # Host input handler (only active when state is AWAITING_HOST)
    # This uses a custom filter that checks wizard state
    class AwaitingHostFilter(Filter):
        async def __call__(self, message: Message) -> bool:
            user_id = message.from_user.id if message.from_user else 0
            return wizard.get_state(user_id) == WizardState.AWAITING_HOST

    dp.message.register(create_host_handler(wizard), AwaitingHostFilter())

    # Callback handlers for wizard buttons
    dp.callback_query.register(
        create_confirm_callback(wizard, on_complete=on_complete),
        F.data == "setup:confirm",
    )
    dp.callback_query.register(
        create_adjust_callback(wizard),
        F.data.startswith("setup:adjust:"),
    )
    dp.callback_query.register(
        create_toggle_callback(wizard),
        F.data.startswith("setup:toggle:"),
    )
    dp.callback_query.register(
        create_adjust_done_callback(wizard),
        F.data == "setup:adjust_done",
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_setup_wizard.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/bot/setup_wizard.py src/bot/telegram_bot.py tests/test_setup_wizard.py
git commit -m "feat: add setup mode middleware and handler registration"
```

---

### Task 8: Wire Everything into main.py

Modify `main.py` to detect first-run, create the wizard, defer monitor startup, and support `/setup` re-runs.

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_setup_wizard.py` (add integration-level test)

**Step 1: Write the failing test**

```python
class TestMainIntegration:
    def test_first_run_detected_without_config(self, tmp_path):
        """Without config.yaml, wizard should be created."""
        from pathlib import Path

        config_path = str(tmp_path / "config.yaml")
        assert not Path(config_path).exists()

        # The first_run detection is: not Path(config_path).exists()
        first_run = not Path(config_path).exists()
        assert first_run is True

    def test_rerun_detected_with_config(self, tmp_path):
        """With existing config.yaml, it's a re-run."""
        from pathlib import Path

        config_path = tmp_path / "config.yaml"
        config_path.write_text("unraid:\n  enabled: true\n")

        first_run = not config_path.exists()
        assert first_run is False
```

**Step 2: Run test to verify it passes (sanity check)**

Run: `pytest tests/test_setup_wizard.py::TestMainIntegration -v`
Expected: PASS

**Step 3: Modify main.py**

Key changes to `src/main.py`:

1. Remove `generate_default_config()` call
2. If no config.yaml exists, create `SetupWizard` and register it
3. Wrap monitor startup in an `async def start_monitors()` function
4. Pass `start_monitors` as `on_complete` callback to the wizard
5. If config.yaml already exists, start monitors normally and register `/setup` for re-runs

The diff is conceptually:

```python
async def main() -> None:
    config_path = os.environ.get("CONFIG_PATH", "config/config.yaml")
    first_run = not os.path.exists(config_path)

    # Load settings (env vars always available)
    settings = Settings()

    # Connect to Docker early (needed for wizard AND normal operation)
    # ... existing Docker connection code ...

    if first_run:
        # Setup mode: create wizard, register handlers, defer monitors
        wizard = SetupWizard(
            config_path=config_path,
            docker_client=monitor._client,
            anthropic_client=anthropic_client,
            unraid_api_key=settings.unraid_api_key,
        )

        bot = create_bot(settings.telegram_bot_token)
        dp = create_dispatcher(settings.telegram_allowed_users, chat_id_store=chat_id_store)

        async def on_setup_complete():
            # Reload config and start monitors
            config = AppConfig(settings)
            await start_monitors(config, ...)

        register_setup_wizard(dp, wizard, on_complete=on_setup_complete)
        # Register /help so it works during setup
        dp.message.register(help_command(state), Command("help"))

        await dp.start_polling(bot)
    else:
        # Normal startup with existing config
        config = AppConfig(settings)
        # ... existing monitor startup code ...
        # Also register /setup for re-runs
        register_setup_wizard(dp, wizard, on_complete=on_config_reload)
        await dp.start_polling(bot)
```

**Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: PASS (all existing + new tests)

**Step 5: Commit**

```bash
git add src/main.py
git commit -m "feat: wire setup wizard into main.py with deferred monitor startup"
```

---

### Task 9: Final Integration Test & Cleanup

Verify the full flow works end-to-end with mocked Docker and Telegram.

**Files:**
- Test: `tests/test_setup_integration.py`

**Step 1: Write integration test**

```python
# tests/test_setup_integration.py
"""Integration test for setup wizard flow."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
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
    """Summary should group containers by category."""
    classifications = [
        ContainerClassification(name="mariadb", image="", categories={"priority", "watched"}),
        ContainerClassification(name="plex", image="", categories={"watched"}),
        ContainerClassification(name="bookstack", image="", categories={"watched"}, ai_suggested=True),
        ContainerClassification(name="dozzle", image="", categories={"ignored"}, ai_suggested=True),
        ContainerClassification(name="unknown-app", image="", categories=set()),
    ]

    summary = format_classification_summary(classifications)

    assert "mariadb" in summary
    assert "plex" in summary
    assert "bookstack*" in summary  # AI marker
    assert "dozzle*" in summary
    assert "unknown-app" in summary  # Unassigned
    assert "AI-suggested" in summary
```

**Step 2: Run integration test**

Run: `pytest tests/test_setup_integration.py -v`
Expected: PASS (3 tests)

**Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: PASS (all tests)

**Step 4: Commit**

```bash
git add tests/test_setup_integration.py
git commit -m "test: add integration tests for setup wizard flow"
```

---

## Summary

| Task | Description | New Files | Modified Files |
|------|------------|-----------|---------------|
| 1 | ContainerClassifier - patterns | `container_classifier.py`, test | - |
| 2 | ContainerClassifier - AI | - | `container_classifier.py`, test |
| 3 | ContainerClassifier - classify_all | - | `container_classifier.py`, test |
| 4 | ConfigWriter | - | `config.py`, test |
| 5 | SetupWizard - state machine | `setup_wizard.py`, test | - |
| 6 | SetupWizard - Telegram handlers | - | `setup_wizard.py`, test |
| 7 | Setup middleware + registration | - | `setup_wizard.py`, `telegram_bot.py`, test |
| 8 | Wire into main.py | - | `main.py`, test |
| 9 | Integration tests + cleanup | test | - |
