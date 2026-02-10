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

    def __init__(self, anthropic_client: "anthropic.AsyncAnthropic | None" = None):
        self._client = anthropic_client

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
