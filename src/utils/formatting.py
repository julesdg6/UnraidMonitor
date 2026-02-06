"""Shared formatting utility functions."""

import re

# Patterns to extract container name from various alert types
_ALERT_PATTERNS = [
    re.compile(r"ERRORS IN[:\s]+(\w+)", re.IGNORECASE),
    re.compile(r"CRASHED[:\s]+(\w+)", re.IGNORECASE),
    re.compile(r"HIGH .+ USAGE[:\s]+(\w+)", re.IGNORECASE),
    re.compile(r"Container[:\s]+(\w+)", re.IGNORECASE),
]


def extract_container_from_alert(text: str) -> str | None:
    """Extract container name from any alert type message.

    Args:
        text: Alert message text.

    Returns:
        Container name if found, None otherwise.
    """
    for pattern in _ALERT_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def format_bytes(bytes_val: int) -> str:
    """Format bytes as human-readable string.

    Args:
        bytes_val: Number of bytes.

    Returns:
        Human-readable string like "1.5GB" or "500MB".
    """
    gb = bytes_val / (1024**3)
    if gb >= 1.0:
        return f"{gb:.1f}GB"
    mb = bytes_val / (1024**2)
    return f"{mb:.0f}MB"


def format_uptime(seconds: int) -> str:
    """Format seconds into human-readable uptime.

    Args:
        seconds: Uptime in seconds.

    Returns:
        Human-readable string like "3d 14h 22m" or "2h 15m" or "45m".
    """
    if seconds < 0:
        return "0m"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def escape_markdown(text: str) -> str:
    """Escape Telegram Markdown V1 special characters.

    Args:
        text: Raw text that may contain *, _, `, [ characters.

    Returns:
        Text with special characters escaped.
    """
    for ch in ("\\", "`", "*", "_", "["):
        text = text.replace(ch, f"\\{ch}")
    return text
