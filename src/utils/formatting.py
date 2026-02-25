"""Shared formatting utility functions."""

import re
from datetime import datetime, timedelta

from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

# Valid Docker container name pattern (alphanumeric, dash, underscore, dot, colon)
# Docker allows: [a-zA-Z0-9][a-zA-Z0-9_.-]* but we also allow colons for compose names
_VALID_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]*$")


def validate_container_name(name: str) -> bool:
    """Validate that a string looks like a valid container name."""
    if not name or len(name) > 256:
        return False
    return bool(_VALID_CONTAINER_NAME.match(name))


def _strip_markdown(text: str) -> str:
    """Strip Markdown V1 formatting characters for plain-text fallback."""
    return text.replace("*", "").replace("`", "").replace("_", "")


async def safe_reply(
    message: Message,
    text: str,
    parse_mode: str = "Markdown",
    **kwargs: object,
) -> Message:
    """Send a message with Markdown, falling back to plain text on parse failure."""
    try:
        return await message.answer(text, parse_mode=parse_mode, **kwargs)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e):
            return await message.answer(_strip_markdown(text), **kwargs)
        raise


async def safe_edit(
    message: Message,
    text: str,
    parse_mode: str = "Markdown",
    **kwargs: object,
) -> Message:
    """Edit a message with Markdown, falling back to plain text on parse failure."""
    try:
        return await message.edit_text(text, parse_mode=parse_mode, **kwargs)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e):
            return await message.edit_text(_strip_markdown(text), **kwargs)
        raise


def format_mute_expiry(expiry: datetime) -> str:
    """Format mute expiry in a human-readable way.

    - Same day: "until 14:30"
    - Tomorrow: "until tomorrow 14:30"
    - Further: "until Feb 26 14:30"
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/London")
    now = datetime.now(tz)

    # Make expiry timezone-aware if naive
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=tz)
    else:
        expiry = expiry.astimezone(tz)

    time_str = expiry.strftime("%H:%M")

    if expiry.date() == now.date():
        return f"until {time_str}"
    elif expiry.date() == (now + timedelta(days=1)).date():
        return f"until tomorrow {time_str}"
    else:
        return f"until {expiry.strftime('%b %d')} {time_str}"

# Common log timestamp patterns to strip for pattern matching
# Matches: 2026-02-25T11:55:11.548437Z, 2026-02-25 11:55:11,548, [2026-02-25T11:55:11]
_LOG_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.,]?\d*Z?\s*"
)


def strip_log_timestamps(line: str) -> str:
    """Strip common timestamp patterns from a log line.

    Removes ISO8601, Python logging, and similar timestamp formats so that
    patterns match future errors regardless of when they occurred.
    """
    return _LOG_TIMESTAMP_RE.sub("", line).strip()


# Patterns to extract container name from various alert types
_ALERT_PATTERNS = [
    re.compile(r"ERRORS IN[:\s]+([\w.\-]+)", re.IGNORECASE),
    re.compile(r"CRASHED[:\s]+([\w.\-]+)", re.IGNORECASE),
    re.compile(r"HIGH .+ USAGE[:\s]+([\w.\-]+)", re.IGNORECASE),
    re.compile(r"Container[:\s]+([\w.\-]+)", re.IGNORECASE),
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


# Telegram message length limit
TELEGRAM_MAX_LENGTH = 4096


def truncate_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH, suffix: str = "\n\n_(truncated)_") -> str:
    """Truncate a message to fit within Telegram's character limit.

    Args:
        text: The message text.
        max_length: Maximum allowed characters (default: 4096).
        suffix: Text appended when truncated.

    Returns:
        The original text if within limit, or truncated text with suffix.
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


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
