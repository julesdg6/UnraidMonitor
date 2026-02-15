"""Rate limiting utilities for API protection."""

import time
from collections import deque


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
        self._minute_timestamps: dict[int, deque[float]] = {}
        self._hour_timestamps: dict[int, deque[float]] = {}

    def is_allowed(self, user_id: int) -> bool:
        """Check if request is allowed and record it if so.

        Args:
            user_id: User identifier.

        Returns:
            True if request is allowed, False if rate limited.
        """
        now = time.monotonic()
        minute_ago = now - 60
        hour_ago = now - 3600

        if user_id not in self._minute_timestamps:
            self._minute_timestamps[user_id] = deque()
            self._hour_timestamps[user_id] = deque()

        # Evict expired entries from the left (O(1) per entry)
        min_dq = self._minute_timestamps[user_id]
        while min_dq and min_dq[0] <= minute_ago:
            min_dq.popleft()

        hr_dq = self._hour_timestamps[user_id]
        while hr_dq and hr_dq[0] <= hour_ago:
            hr_dq.popleft()

        # Periodically clean empty entries from inactive users
        if len(self._minute_timestamps) > 100:
            self._cleanup_empty()

        # Check limits
        if len(min_dq) >= self._max_per_minute:
            return False
        if len(hr_dq) >= self._max_per_hour:
            return False

        # Record this request
        min_dq.append(now)
        hr_dq.append(now)
        return True

    def get_retry_after(self, user_id: int) -> int:
        """Get seconds until user can make another request.

        Args:
            user_id: User identifier.

        Returns:
            Seconds to wait, or 0 if not rate limited.
        """
        now = time.monotonic()
        minute_ago = now - 60

        min_dq = self._minute_timestamps.get(user_id)
        if not min_dq:
            return 0

        # Evict expired entries
        while min_dq and min_dq[0] <= minute_ago:
            min_dq.popleft()

        if len(min_dq) >= self._max_per_minute:
            oldest = min_dq[0]
            wait_until = oldest + 60
            return max(0, int(wait_until - now))

        return 0

    def _cleanup_empty(self) -> None:
        """Remove entries for users with no recent activity."""
        empty = [uid for uid, dq in self._minute_timestamps.items() if not dq]
        for uid in empty:
            del self._minute_timestamps[uid]
            self._hour_timestamps.pop(uid, None)
