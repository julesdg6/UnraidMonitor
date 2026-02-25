"""Tests for the formatting utility functions."""


def test_format_bytes_gb():
    """Test format_bytes with gigabyte values."""
    from src.utils.formatting import format_bytes

    assert format_bytes(1_073_741_824) == "1.0GB"  # 1 GB exactly
    assert format_bytes(4_000_000_000) == "3.7GB"  # ~3.7 GB


def test_format_bytes_mb():
    """Test format_bytes with megabyte values."""
    from src.utils.formatting import format_bytes

    assert format_bytes(524_288_000) == "500MB"  # 500 MB
    assert format_bytes(1_000_000) == "1MB"  # ~1 MB


def test_format_bytes_small_gb():
    """Test format_bytes at the GB boundary."""
    from src.utils.formatting import format_bytes

    # Just under 1 GB should show MB
    assert format_bytes(1_073_741_823) == "1024MB"
    # Exactly 1 GB should show GB
    assert format_bytes(1_073_741_824) == "1.0GB"


def test_format_bytes_large_values():
    """Test format_bytes with larger GB values."""
    from src.utils.formatting import format_bytes

    assert format_bytes(8_589_934_592) == "8.0GB"  # 8 GB
    assert format_bytes(16_000_000_000) == "14.9GB"  # ~15 GB


def test_format_uptime_days_hours_minutes():
    """Test format_uptime with days, hours, and minutes."""
    from src.utils.formatting import format_uptime

    # 3 days, 14 hours, 22 minutes
    seconds = 3 * 86400 + 14 * 3600 + 22 * 60
    assert format_uptime(seconds) == "3d 14h 22m"


def test_format_uptime_hours_minutes():
    """Test format_uptime with just hours and minutes."""
    from src.utils.formatting import format_uptime

    seconds = 2 * 3600 + 15 * 60
    assert format_uptime(seconds) == "2h 15m"


def test_format_uptime_minutes_only():
    """Test format_uptime with just minutes."""
    from src.utils.formatting import format_uptime

    assert format_uptime(45 * 60) == "45m"


def test_format_uptime_zero():
    """Test format_uptime with zero seconds."""
    from src.utils.formatting import format_uptime

    assert format_uptime(0) == "0m"


def test_format_uptime_large():
    """Test format_uptime with many days."""
    from src.utils.formatting import format_uptime

    # 30 days, 0 hours, 0 minutes
    assert format_uptime(30 * 86400) == "30d"


def test_format_uptime_days_minutes_no_hours():
    """Test format_uptime with days and minutes but no hours."""
    from src.utils.formatting import format_uptime

    seconds = 1 * 86400 + 5 * 60
    assert format_uptime(seconds) == "1d 5m"


def test_strip_log_timestamps_iso8601():
    """Test stripping ISO8601 timestamps from log lines."""
    from src.utils.formatting import strip_log_timestamps

    line = "[error] 2026-02-25T11:55:11.548437Z nonode@nohost <0.3827709.0>"
    result = strip_log_timestamps(line)
    assert result == "[error] nonode@nohost <0.3827709.0>"


def test_strip_log_timestamps_python_logging():
    """Test stripping Python-style timestamps."""
    from src.utils.formatting import strip_log_timestamps

    line = "2026-02-25 11:55:11,548 - src.main - ERROR - something broke"
    result = strip_log_timestamps(line)
    assert result == "- src.main - ERROR - something broke"


def test_strip_log_timestamps_no_timestamp():
    """Test that lines without timestamps are unchanged."""
    from src.utils.formatting import strip_log_timestamps

    line = "[error] nonode@nohost connection refused"
    result = strip_log_timestamps(line)
    assert result == line


def test_strip_log_timestamps_multiple():
    """Test stripping multiple timestamps from a line."""
    from src.utils.formatting import strip_log_timestamps

    line = "2026-02-25T11:55:11Z start 2026-02-25T12:00:00Z end"
    result = strip_log_timestamps(line)
    assert result == "start end"


def test_strip_log_timestamps_iso_no_fractional():
    """Test stripping ISO timestamp without fractional seconds."""
    from src.utils.formatting import strip_log_timestamps

    line = "[warn] 2026-02-25T11:55:11Z some warning"
    result = strip_log_timestamps(line)
    assert result == "[warn] some warning"
