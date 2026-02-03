"""Rate limiting utilities for API protection."""

from collections import defaultdict
from datetime import datetime, timedelta


class PerUserRateLimiter:
    """Rate limiter that tracks requests per user."""

    def __init__(
        self,
        max_per_minute: int = 10,
        max_per_hour: int = 60,
    ):
        """Initialize rate limiter.

        Args:
            max_per_minute: Maximum requests allowed per minute per user.
            max_per_hour: Maximum requests allowed per hour per user.
        """
        self._max_per_minute = max_per_minute
        self._max_per_hour = max_per_hour
        self._minute_timestamps: dict[int, list[datetime]] = defaultdict(list)
        self._hour_timestamps: dict[int, list[datetime]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        """Check if request is allowed and record it if so.

        Args:
            user_id: User identifier.

        Returns:
            True if request is allowed, False if rate limited.
        """
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        hour_ago = now - timedelta(hours=1)

        # Clean old entries
        self._minute_timestamps[user_id] = [
            t for t in self._minute_timestamps[user_id] if t > minute_ago
        ]
        self._hour_timestamps[user_id] = [
            t for t in self._hour_timestamps[user_id] if t > hour_ago
        ]

        # Check limits
        if len(self._minute_timestamps[user_id]) >= self._max_per_minute:
            return False
        if len(self._hour_timestamps[user_id]) >= self._max_per_hour:
            return False

        # Record this request
        self._minute_timestamps[user_id].append(now)
        self._hour_timestamps[user_id].append(now)
        return True

    def get_retry_after(self, user_id: int) -> int:
        """Get seconds until user can make another request.

        Args:
            user_id: User identifier.

        Returns:
            Seconds to wait, or 0 if not rate limited.
        """
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)

        # Clean and check minute limit
        self._minute_timestamps[user_id] = [
            t for t in self._minute_timestamps[user_id] if t > minute_ago
        ]

        if len(self._minute_timestamps[user_id]) >= self._max_per_minute:
            oldest = min(self._minute_timestamps[user_id])
            wait_until = oldest + timedelta(minutes=1)
            return max(0, int((wait_until - now).total_seconds()))

        return 0
