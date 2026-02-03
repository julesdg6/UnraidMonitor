"""Input sanitization utilities for AI prompt injection prevention."""

import re


# Pre-compile patterns for performance
_INJECTION_PATTERNS = [
    # Attempts to add new instructions
    (re.compile(r"(?i)\b(ignore|disregard|forget)\s+(all\s+)?(previous|above|prior)\s+(instructions?|context|prompts?)"), "[FILTERED]"),
    # Attempts to impersonate system prompts
    (re.compile(r"(?i)^(system|assistant|human|user):\s*", re.MULTILINE), "data: "),
    # Attempts to create new roles
    (re.compile(r"(?i)\[?(system|assistant)\]?\s*:"), "[data]:"),
    # XML/markdown injection attempts that might affect prompt parsing
    (re.compile(r"<\s*/?(?:system|prompt|instruction|context|tool_result|function_result|result)[^>]*>", re.IGNORECASE), "[tag]"),
    # Block attempts to simulate tool/function responses
    (re.compile(r"(?i)\b(tool_use|tool_result|function_call|function_result)\b"), "[FILTERED]"),
    # Block attempts to reference AI systems
    (re.compile(r"(?i)\b(anthropic|claude|openai|chatgpt|gpt-4|gpt-3)\b"), "[FILTERED]"),
    # Block "act as" / "pretend to be" instructions
    (re.compile(r"(?i)\b(act\s+as|pretend\s+(to\s+be|you\s+are)|you\s+are\s+now|roleplay\s+as)\b"), "[FILTERED]"),
    # Block jailbreak-style instructions
    (re.compile(r"(?i)\b(jailbreak|dan\s+mode|developer\s+mode|unrestricted\s+mode)\b"), "[FILTERED]"),
    # HTML comments that could hide text
    (re.compile(r"<!--.*?-->", re.DOTALL), ""),
]

# Maximum length per line to prevent single-line flooding
_MAX_LINE_LENGTH = 1000


def sanitize_for_prompt(text: str, max_length: int = 10000) -> str:
    """Sanitize user-controlled text before including in AI prompts.

    This helps mitigate prompt injection attacks where container logs or
    names might contain text designed to manipulate the AI model.

    Args:
        text: The text to sanitize (container name, logs, error messages).
        max_length: Maximum length to allow (truncate if longer).

    Returns:
        Sanitized text safe for inclusion in prompts.
    """
    if not text:
        return ""

    # Truncate individual lines to prevent single-line flooding
    lines = text.split("\n")
    truncated_lines = []
    for line in lines:
        if len(line) > _MAX_LINE_LENGTH:
            truncated_lines.append(line[:_MAX_LINE_LENGTH] + "...")
        else:
            truncated_lines.append(line)
    text = "\n".join(truncated_lines)

    # Truncate total length
    if len(text) > max_length:
        text = text[:max_length] + "\n... (truncated)"

    # Apply injection pattern filters
    for pattern, replacement in _INJECTION_PATTERNS:
        text = pattern.sub(replacement, text)

    return text


# Pre-compile patterns for sensitive data redaction
_SENSITIVE_DATA_PATTERNS = [
    # API keys and tokens (common formats)
    (re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|pwd|credentials?)\s*[=:]\s*[\"']?[\w\-\.]{8,}[\"']?"), r"\1=***REDACTED***"),
    # Bearer tokens
    (re.compile(r"(?i)Bearer\s+[\w\-\.]+"), "Bearer ***REDACTED***"),
    # Basic auth in URLs
    (re.compile(r"://[^:]+:[^@]+@"), "://***:***@"),
    # Connection strings (database URLs)
    (re.compile(r"(?i)(mysql|postgres|postgresql|mongodb|redis|amqp)://[^\s]+"), r"\1://***REDACTED***"),
    # AWS-style keys
    (re.compile(r"(?i)(AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}"), "***AWS_KEY_REDACTED***"),
    # Generic hex tokens (32+ chars, likely secrets)
    (re.compile(r"\b[a-fA-F0-9]{32,}\b"), "***HEX_REDACTED***"),
    # JWT tokens (three base64 segments separated by dots)
    (re.compile(r"eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*"), "***JWT_REDACTED***"),
]


def sanitize_logs_for_display(logs: str) -> str:
    """Remove potentially sensitive data from logs before display.

    This helps prevent accidental exposure of secrets, tokens, and credentials
    that may appear in container logs.

    Args:
        logs: Raw log text.

    Returns:
        Logs with sensitive data redacted.
    """
    if not logs:
        return ""

    for pattern, replacement in _SENSITIVE_DATA_PATTERNS:
        logs = pattern.sub(replacement, logs)

    return logs


def sanitize_container_name(name: str) -> str:
    """Sanitize a container name for use in prompts.

    Container names should be alphanumeric with dashes/underscores,
    but malicious names could contain injection attempts.

    Args:
        name: Container name to sanitize.

    Returns:
        Sanitized container name.
    """
    # Container names should be relatively short
    return sanitize_for_prompt(name, max_length=256)


def sanitize_logs(logs: str, max_length: int = 8000) -> str:
    """Sanitize container logs for use in prompts.

    Logs are the main vector for prompt injection since they contain
    arbitrary output that could be crafted by an attacker.

    Args:
        logs: Log text to sanitize.
        max_length: Maximum length to allow.

    Returns:
        Sanitized log text.
    """
    return sanitize_for_prompt(logs, max_length=max_length)
