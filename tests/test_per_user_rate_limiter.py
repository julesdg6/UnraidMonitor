"""Tests for PerUserRateLimiter (F44)."""

import time
from unittest.mock import patch

from src.utils.rate_limiter import PerUserRateLimiter


class TestPerUserRateLimiter:
    """Tests for the per-user rate limiter."""

    def test_allows_first_request(self):
        limiter = PerUserRateLimiter(max_per_minute=5, max_per_hour=20)
        assert limiter.is_allowed(user_id=1) is True

    def test_allows_up_to_minute_limit(self):
        limiter = PerUserRateLimiter(max_per_minute=3, max_per_hour=100)
        assert limiter.is_allowed(1) is True
        assert limiter.is_allowed(1) is True
        assert limiter.is_allowed(1) is True
        # Fourth should be rejected
        assert limiter.is_allowed(1) is False

    def test_allows_up_to_hour_limit(self):
        limiter = PerUserRateLimiter(max_per_minute=100, max_per_hour=3)
        assert limiter.is_allowed(1) is True
        assert limiter.is_allowed(1) is True
        assert limiter.is_allowed(1) is True
        # Fourth should be rejected by hourly limit
        assert limiter.is_allowed(1) is False

    def test_multi_user_isolation(self):
        """Different users have independent limits."""
        limiter = PerUserRateLimiter(max_per_minute=2, max_per_hour=100)
        assert limiter.is_allowed(1) is True
        assert limiter.is_allowed(1) is True
        assert limiter.is_allowed(1) is False  # User 1 exhausted

        # User 2 should still be allowed
        assert limiter.is_allowed(2) is True
        assert limiter.is_allowed(2) is True
        assert limiter.is_allowed(2) is False  # User 2 now exhausted

    def test_minute_window_expires(self):
        """Requests should be allowed again after the minute window passes."""
        limiter = PerUserRateLimiter(max_per_minute=2, max_per_hour=100)

        base_time = 1000.0
        with patch("src.utils.rate_limiter.time.monotonic", return_value=base_time):
            assert limiter.is_allowed(1) is True
            assert limiter.is_allowed(1) is True
            assert limiter.is_allowed(1) is False

        # Jump forward 61 seconds
        with patch("src.utils.rate_limiter.time.monotonic", return_value=base_time + 61):
            assert limiter.is_allowed(1) is True

    def test_get_retry_after_not_limited(self):
        limiter = PerUserRateLimiter(max_per_minute=5, max_per_hour=100)
        assert limiter.get_retry_after(user_id=1) == 0

    def test_get_retry_after_when_limited(self):
        limiter = PerUserRateLimiter(max_per_minute=2, max_per_hour=100)

        base_time = 1000.0
        with patch("src.utils.rate_limiter.time.monotonic", return_value=base_time):
            limiter.is_allowed(1)
            limiter.is_allowed(1)

        # At base_time + 30, still rate limited, should return ~30s
        with patch("src.utils.rate_limiter.time.monotonic", return_value=base_time + 30):
            retry = limiter.get_retry_after(1)
            assert retry > 0
            assert retry <= 30

    def test_get_retry_after_unknown_user(self):
        limiter = PerUserRateLimiter(max_per_minute=5, max_per_hour=100)
        assert limiter.get_retry_after(user_id=999) == 0

    def test_cleanup_empty_entries(self):
        """Empty entries should be cleaned when threshold is exceeded."""
        from collections import deque

        limiter = PerUserRateLimiter(max_per_minute=1, max_per_hour=100)

        # Manually inject 101 empty deques to simulate users whose entries expired
        for uid in range(101):
            limiter._minute_timestamps[uid] = deque()
            limiter._hour_timestamps[uid] = deque()

        assert len(limiter._minute_timestamps) == 101

        # Next is_allowed call should trigger _cleanup_empty (> 100 entries)
        base_time = 1000.0
        with patch("src.utils.rate_limiter.time.monotonic", return_value=base_time):
            limiter.is_allowed(999)

        # All 101 empty entries should have been removed, leaving only uid=999
        assert len(limiter._minute_timestamps) <= 2

    def test_default_limits(self):
        limiter = PerUserRateLimiter()
        # Default is 10 per minute, 60 per hour
        for _ in range(10):
            assert limiter.is_allowed(1) is True
        assert limiter.is_allowed(1) is False
