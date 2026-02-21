"""Container classifier using pattern matching and optional AI."""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.utils.api_errors import handle_llm_error
from src.utils.sanitize import sanitize_container_name

if TYPE_CHECKING:
    from src.services.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"priority", "protected", "watched", "killable", "ignored"}

CLASSIFY_PROMPT = """Classify these Docker containers into monitoring categories.

Containers:
{container_list}

Valid categories: priority (critical services, never kill), protected (no remote control), watched (monitor logs), killable (can be stopped to free memory), ignored (don't monitor)

A container can have multiple categories. Most containers should be "watched".
Return ONLY a JSON array (no markdown, no explanation):
[{{"name": "container_name", "categories": ["watched"], "description": "Brief description"}}]"""


@dataclass
class ContainerClassification:
    """Classification result for a single container."""
    name: str
    image: str
    categories: set[str] = field(default_factory=set)
    description: str = ""
    ai_suggested: bool = False


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
        ["qbittorrent", "qbit", "sabnzbd", "sab", "nzbget", "deluge", "transmission",
         "rtorrent", "flood"],
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

    def __init__(self, provider: "LLMProvider | None" = None):
        self._provider = provider

    def classify_by_pattern(self, name: str, image: str) -> ContainerClassification:
        """Classify a container using pattern matching only."""
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

    async def classify_batch_with_ai(
        self, unclassified: list[ContainerClassification]
    ) -> list[ContainerClassification]:
        """Classify containers using Haiku AI."""
        if not self._provider or not unclassified:
            return unclassified

        container_list = "\n".join(
            f"- {sanitize_container_name(c.name)} (image: {sanitize_container_name(c.image)})"
            for c in unclassified
        )
        prompt = CLASSIFY_PROMPT.format(container_list=container_list)

        try:
            response = await self._provider.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
            )
            text = response.text

            # Extract JSON array from response
            json_match = re.search(r"\[.*\]", text, re.DOTALL)
            if not json_match:
                logger.error(f"No JSON array found in AI response: {text}")
                return unclassified

            ai_results = json.loads(json_match.group())
            by_name = {c.name: c for c in unclassified}

            for item in ai_results:
                name = item.get("name", "")
                if name not in by_name:
                    continue
                categories = set(item.get("categories", []))
                valid = categories & VALID_CATEGORIES
                by_name[name].categories = valid
                by_name[name].description = item.get("description", "")
                by_name[name].ai_suggested = True

            return unclassified

        except Exception as e:
            error_result = handle_llm_error(e)
            logger.log(error_result.log_level, f"AI classification failed: {e}")
            return unclassified

    async def classify_all(
        self, containers: list[tuple[str, str, str]]
    ) -> list[ContainerClassification]:
        """Classify all containers using pattern matching + AI.

        Args:
            containers: List of (name, image, status) tuples.
        """
        classified = []
        unclassified = []

        for name, image, status in containers:
            result = self.classify_by_pattern(name, image)
            if result.categories:
                classified.append(result)
            else:
                unclassified.append(result)

        if unclassified:
            ai_results = await self.classify_batch_with_ai(unclassified)
            classified.extend(ai_results)

        return classified
