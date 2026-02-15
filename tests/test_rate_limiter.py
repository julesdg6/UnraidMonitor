import pytest
from datetime import datetime, timedelta


def test_rate_limiter_allows_first_event():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    assert limiter.should_alert("radarr") is True


def test_rate_limiter_blocks_during_cooldown():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    limiter.record_alert("radarr")

    assert limiter.should_alert("radarr") is False


def test_rate_limiter_allows_after_cooldown():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    # Simulate alert 20 minutes ago
    limiter._last_alert["radarr"] = datetime.now() - timedelta(minutes=20)

    assert limiter.should_alert("radarr") is True


def test_rate_limiter_tracks_containers_independently():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    limiter.record_alert("radarr")

    assert limiter.should_alert("radarr") is False
    assert limiter.should_alert("sonarr") is True


def test_rate_limiter_records_suppressed_count():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    limiter.record_alert("radarr")
    limiter.record_suppressed("radarr")
    limiter.record_suppressed("radarr")

    assert limiter.get_suppressed_count("radarr") == 2


def test_rate_limiter_resets_suppressed_on_alert():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    limiter.record_alert("radarr")
    limiter.record_suppressed("radarr")
    limiter.record_suppressed("radarr")

    # Simulate cooldown expired
    limiter._last_alert["radarr"] = datetime.now() - timedelta(minutes=20)

    # This should reset the suppressed count
    limiter.record_alert("radarr")

    assert limiter.get_suppressed_count("radarr") == 0


def test_rate_limiter_auto_cleans_stale_entries():
    """Stale entries should be cleaned automatically via should_alert()."""
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    # Add a stale entry (25 hours old)
    limiter._last_alert["old_container"] = datetime.now() - timedelta(hours=25)
    limiter._suppressed_count["old_container"] = 5

    # Trigger enough calls to hit the cleanup threshold
    for i in range(101):
        limiter.should_alert(f"container_{i}")

    # Stale entry should have been cleaned
    assert "old_container" not in limiter._last_alert
    assert "old_container" not in limiter._suppressed_count
