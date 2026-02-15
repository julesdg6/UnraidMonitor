# Optimization Pass Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement 22 optimizations across API cost, performance, memory leaks, I/O, and concurrency for UnraidMonitor v0.8.2.

**Architecture:** Incremental changes to existing modules. No new files. Each task is independent and can be committed separately. Grouped by risk (low-risk first) to catch regressions early.

**Tech Stack:** Python 3.11, asyncio, collections.deque, Anthropic prompt caching, pytest-asyncio

---

### Task 1: Config Property Caching

**Files:**
- Modify: `src/config.py:298-381`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_config_properties_return_cached_instances():
    """Config properties should return the same object on repeated access."""
    from unittest.mock import patch
    from src.config import Settings, AppConfig

    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        settings = Settings(_env_file=None)
        config = AppConfig(settings)

        # Same property access should return the same object
        assert config.ai is config.ai
        assert config.bot is config.bot
        assert config.docker is config.docker
        assert config.resource_monitoring is config.resource_monitoring
        assert config.unraid is config.unraid
        assert config.memory_management is config.memory_management
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_config_properties_return_cached_instances -v`
Expected: FAIL — each property call creates a new object, so `is` check fails.

**Step 3: Implement the fix**

In `src/config.py`, modify `AppConfig.__init__` (line 301-308) to cache config objects:

```python
def __init__(self, settings: Settings):
    self._settings = settings
    self._yaml_config = load_yaml_config(settings.config_path)
    # Cache config objects — these are read-only after startup
    self._ai = AIConfig.from_dict(self._yaml_config.get("ai", {}))
    self._bot_config = BotConfig.from_dict(self._yaml_config.get("bot", {}))
    self._docker = DockerConfig.from_dict(self._yaml_config.get("docker", {}))
    self._resource_monitoring = ResourceConfig.from_dict(
        self._yaml_config.get("resource_monitoring", {})
    )
    self._unraid = UnraidConfig.from_dict(self._yaml_config.get("unraid", {}))
    self._memory_management = MemoryConfig.from_dict(
        self._yaml_config.get("memory_management", {})
    )
```

Then update each property to return the cached instance:

```python
@property
def ai(self) -> AIConfig:
    """Get AI/Claude API configuration."""
    return self._ai

@property
def bot(self) -> BotConfig:
    """Get bot display and behaviour configuration."""
    return self._bot_config

@property
def docker(self) -> DockerConfig:
    """Get Docker connection configuration."""
    return self._docker

@property
def resource_monitoring(self) -> ResourceConfig:
    """Get resource monitoring configuration."""
    return self._resource_monitoring

@property
def unraid(self) -> UnraidConfig:
    """Get Unraid configuration."""
    return self._unraid

@property
def memory_management(self) -> MemoryConfig:
    """Get memory management configuration."""
    return self._memory_management
```

**Step 4: Run tests**

Run: `pytest tests/test_config.py -v`
Expected: All PASS

**Step 5: Run full suite to check for regressions**

Run: `pytest tests/ -x -q`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "perf: cache config property objects in AppConfig.__init__"
```

---

### Task 2: Per-User Rate Limiter — List to Deque

**Files:**
- Modify: `src/utils/rate_limiter.py`
- Test: `tests/test_nl_processor.py` (existing PerUserRateLimiter tests)

**Step 1: Write the failing test**

Add to a new section in `tests/test_nl_processor.py` or create a test inline:

```python
def test_per_user_rate_limiter_uses_deque():
    """Rate limiter should use deque internally for O(1) eviction."""
    from collections import deque
    from src.utils.rate_limiter import PerUserRateLimiter

    limiter = PerUserRateLimiter(max_per_minute=5, max_per_hour=20)
    limiter.is_allowed(1)  # Trigger creation of internal structures

    assert isinstance(limiter._minute_timestamps[1], deque)
    assert isinstance(limiter._hour_timestamps[1], deque)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_nl_processor.py::test_per_user_rate_limiter_uses_deque -v`
Expected: FAIL — currently uses `list`, not `deque`.

**Step 3: Implement the fix**

Replace `src/utils/rate_limiter.py` contents:

```python
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
        self._max_per_minute = max_per_minute
        self._max_per_hour = max_per_hour
        self._minute_timestamps: dict[int, deque[float]] = {}
        self._hour_timestamps: dict[int, deque[float]] = {}

    def is_allowed(self, user_id: int) -> bool:
        """Check if request is allowed and record it if so."""
        now = time.monotonic()
        minute_ago = now - 60
        hour_ago = now - 3600

        # Ensure deques exist for this user
        if user_id not in self._minute_timestamps:
            self._minute_timestamps[user_id] = deque()
            self._hour_timestamps[user_id] = deque()

        # Evict expired entries from the left (oldest first)
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
        """Get seconds until user can make another request."""
        now = time.monotonic()
        minute_ago = now - 60

        min_dq = self._minute_timestamps.get(user_id)
        if not min_dq:
            return 0

        # Evict expired
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
```

Note: Changed from `datetime` to `time.monotonic()` for efficiency — monotonic floats are cheaper than datetime objects and immune to clock adjustments.

**Step 4: Run tests**

Run: `pytest tests/test_nl_processor.py -v`
Expected: All PASS

**Step 5: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/utils/rate_limiter.py tests/test_nl_processor.py
git commit -m "perf: replace list with deque in PerUserRateLimiter for O(1) eviction"
```

---

### Task 3: Conversation Memory — List to Deque

**Files:**
- Modify: `src/services/nl_processor.py:14-43`
- Test: `tests/test_nl_processor.py`

**Step 1: Write the failing test**

Add to `tests/test_nl_processor.py` in `TestConversationMemory`:

```python
def test_messages_is_deque(self):
    from collections import deque
    memory = ConversationMemory(user_id=123)
    assert isinstance(memory.messages, deque)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_nl_processor.py::TestConversationMemory::test_messages_is_deque -v`
Expected: FAIL

**Step 3: Implement the fix**

In `src/services/nl_processor.py`, modify `ConversationMemory`:

```python
from collections import deque

@dataclass
class ConversationMemory:
    """Stores conversation history for a single user."""

    user_id: int
    max_exchanges: int = 5
    messages: deque = field(default_factory=deque)
    last_activity: datetime = field(default_factory=lambda: datetime.now())
    pending_action: dict[str, Any] | None = None

    def __post_init__(self):
        """Set maxlen on deque after dataclass init."""
        if not isinstance(self.messages, deque) or self.messages.maxlen is None:
            self.messages = deque(self.messages, maxlen=self.max_exchanges * 2)

    def add_exchange(self, user_message: str, assistant_message: str) -> None:
        """Add a user/assistant exchange. Oldest auto-evicted by deque maxlen."""
        self.messages.append({"role": "user", "content": user_message})
        self.messages.append({"role": "assistant", "content": assistant_message})
        self.last_activity = datetime.now()

    def get_messages(self) -> list[dict[str, str]]:
        """Return a copy of messages for use in API calls."""
        return list(self.messages)

    def clear(self) -> None:
        """Clear all messages and pending action."""
        self.messages.clear()
        self.pending_action = None
```

**Step 4: Run tests**

Run: `pytest tests/test_nl_processor.py::TestConversationMemory -v`
Expected: All PASS

**Step 5: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/services/nl_processor.py tests/test_nl_processor.py
git commit -m "perf: use deque for ConversationMemory to avoid list slicing"
```

---

### Task 4: Log Watcher Pattern Cache — Fix Cache Key

**Files:**
- Modify: `src/monitors/log_watcher.py:19-40`
- Test: `tests/test_log_watcher.py`

**Step 1: Write the failing test**

Add to `tests/test_log_watcher.py`:

```python
def test_matches_error_pattern_cache_survives_list_rebuild():
    """Cache should work with new list objects containing identical patterns."""
    from src.monitors.log_watcher import matches_error_pattern

    errors1 = ["error", "fatal"]
    ignores1 = ["debug"]

    # First call with original lists
    result1 = matches_error_pattern("something error happened", errors1, ignores1)
    assert result1 is True

    # Create NEW list objects with SAME content (simulates config reload)
    errors2 = list(errors1)  # New object, same content
    ignores2 = list(ignores1)

    # Should still use cached lowercased patterns (same content)
    result2 = matches_error_pattern("something error happened", errors2, ignores2)
    assert result2 is True
```

**Step 2: Run test to verify it fails (or note current behavior)**

Run: `pytest tests/test_log_watcher.py::test_matches_error_pattern_cache_survives_list_rebuild -v`
Expected: May pass (cache miss just means re-computation), but we can verify cache behavior with a more targeted test. The real fix is correctness — let's just implement and verify.

**Step 3: Implement the fix**

In `src/monitors/log_watcher.py`, change the cache key from `id()` to `tuple()`:

```python
def matches_error_pattern(
    line: str,
    error_patterns: list[str],
    ignore_patterns: list[str],
    *,
    _cache: dict[tuple[tuple[str, ...], tuple[str, ...]], tuple[list[str], list[str]]] = {},
) -> bool:
    """Check if a log line matches any error pattern and no ignore pattern."""
    if _SELF_LOG_RE.match(line):
        return False

    line_lower = line.lower()

    # Cache lowercased patterns (keyed by content, not identity)
    cache_key = (tuple(error_patterns), tuple(ignore_patterns))
    if cache_key not in _cache:
        _cache[cache_key] = (
            [p.lower() for p in error_patterns],
            [p.lower() for p in ignore_patterns],
        )
    error_lower, ignore_lower = _cache[cache_key]

    for pattern in ignore_lower:
        if pattern in line_lower:
            return False

    for pattern in error_lower:
        if pattern in line_lower:
            return True

    return False
```

**Step 4: Run tests**

Run: `pytest tests/test_log_watcher.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/monitors/log_watcher.py tests/test_log_watcher.py
git commit -m "fix: use content-based cache key in log watcher pattern matching"
```

---

### Task 5: Confirmation Manager — Reduce Cleanup Frequency

**Files:**
- Modify: `src/bot/confirmation.py`
- Test: `tests/test_confirmation.py`

**Step 1: Write the failing test**

Add to `tests/test_confirmation.py`:

```python
def test_cleanup_does_not_run_every_request():
    """Cleanup should only run periodically, not on every request()."""
    from unittest.mock import patch
    from src.bot.confirmation import ConfirmationManager

    manager = ConfirmationManager(timeout_seconds=60)

    with patch.object(manager, '_cleanup_expired') as mock_cleanup:
        # First call should trigger cleanup
        manager.request(1, "restart", "plex")
        first_call_count = mock_cleanup.call_count

        # Rapid subsequent calls should NOT all trigger cleanup
        for i in range(10):
            manager.request(i + 2, "restart", "plex")

        # Should NOT have called cleanup 11 times
        assert mock_cleanup.call_count < 6
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_confirmation.py::test_cleanup_does_not_run_every_request -v`
Expected: FAIL — currently calls cleanup on every `request()`.

**Step 3: Implement the fix**

In `src/bot/confirmation.py`:

```python
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

_CLEANUP_INTERVAL = 60  # seconds


@dataclass
class PendingConfirmation:
    """A pending confirmation waiting for user response."""
    action: str
    container_name: str
    expires_at: datetime


class ConfirmationManager:
    """Manages pending confirmations for control commands."""

    def __init__(self, timeout_seconds: int = 60):
        self.timeout_seconds = timeout_seconds
        self._pending: dict[int, PendingConfirmation] = {}
        self._last_cleanup: float = 0.0

    def request(self, user_id: int, action: str, container_name: str) -> None:
        """Store a pending confirmation for a user."""
        self._maybe_cleanup()
        expires_at = datetime.now() + timedelta(seconds=self.timeout_seconds)
        self._pending[user_id] = PendingConfirmation(
            action=action,
            container_name=container_name,
            expires_at=expires_at,
        )

    def _maybe_cleanup(self) -> None:
        """Run cleanup at most once per interval."""
        now = time.monotonic()
        if now - self._last_cleanup < _CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        self._cleanup_expired()

    def _cleanup_expired(self) -> None:
        """Remove all expired entries."""
        now = datetime.now()
        expired = [uid for uid, p in self._pending.items() if now > p.expires_at]
        for uid in expired:
            del self._pending[uid]

    def get_pending(self, user_id: int) -> PendingConfirmation | None:
        """Get pending confirmation for user if not expired."""
        pending = self._pending.get(user_id)
        if pending is None:
            return None
        if datetime.now() > pending.expires_at:
            del self._pending[user_id]
            return None
        return pending

    def confirm(self, user_id: int) -> PendingConfirmation | None:
        """Get and clear pending confirmation if valid."""
        pending = self.get_pending(user_id)
        if pending is not None:
            del self._pending[user_id]
        return pending
```

**Step 4: Run tests**

Run: `pytest tests/test_confirmation.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/bot/confirmation.py tests/test_confirmation.py
git commit -m "perf: throttle confirmation manager cleanup to once per minute"
```

---

### Task 6: Alert Rate Limiter — Auto-Cleanup

**Files:**
- Modify: `src/alerts/rate_limiter.py`
- Test: `tests/test_rate_limiter.py`

**Step 1: Write the failing test**

Add to `tests/test_rate_limiter.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_rate_limiter.py::test_rate_limiter_auto_cleans_stale_entries -v`
Expected: FAIL — `should_alert()` never calls `cleanup_stale()`.

**Step 3: Implement the fix**

In `src/alerts/rate_limiter.py`, add periodic cleanup to `should_alert()`:

```python
from datetime import datetime, timedelta


class RateLimiter:
    """Rate limiter to prevent alert spam."""

    _STALE_THRESHOLD = timedelta(hours=24)
    _CLEANUP_EVERY_N_CHECKS = 100

    def __init__(self, cooldown_seconds: int = 900):
        self.cooldown_seconds = cooldown_seconds
        self._last_alert: dict[str, datetime] = {}
        self._suppressed_count: dict[str, int] = {}
        self._check_count: int = 0

    def should_alert(self, container_name: str) -> bool:
        """Check if an alert should be sent for this container."""
        self._check_count += 1
        if self._check_count >= self._CLEANUP_EVERY_N_CHECKS:
            self._check_count = 0
            self.cleanup_stale()

        last = self._last_alert.get(container_name)
        if last is None:
            return True

        elapsed = datetime.now() - last
        return elapsed >= timedelta(seconds=self.cooldown_seconds)

    def record_alert(self, container_name: str) -> None:
        """Record that an alert was sent."""
        self._last_alert[container_name] = datetime.now()
        self._suppressed_count[container_name] = 0

    def record_suppressed(self, container_name: str) -> None:
        """Record that an alert was suppressed."""
        current = self._suppressed_count.get(container_name, 0)
        self._suppressed_count[container_name] = current + 1

    def get_suppressed_count(self, container_name: str) -> int:
        """Get count of suppressed alerts since last sent alert."""
        return self._suppressed_count.get(container_name, 0)

    def cleanup_stale(self) -> int:
        """Remove entries older than the stale threshold."""
        now = datetime.now()
        stale_keys = [
            key for key, ts in self._last_alert.items()
            if now - ts > self._STALE_THRESHOLD
        ]
        for key in stale_keys:
            del self._last_alert[key]
            self._suppressed_count.pop(key, None)
        return len(stale_keys)
```

**Step 4: Run tests**

Run: `pytest tests/test_rate_limiter.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/alerts/rate_limiter.py tests/test_rate_limiter.py
git commit -m "perf: auto-cleanup stale entries in alert RateLimiter"
```

---

### Task 7: Memory Leak Fixes (3 items bundled — small changes)

**Files:**
- Modify: `src/unraid/monitors/system_monitor.py:42, 68-119`
- Modify: `src/monitors/memory_monitor.py:98-128`
- Modify: `src/services/diagnostic.py:194-214`
- Test: `tests/test_unraid_system_monitor.py`, `tests/test_memory_monitor.py`, `tests/test_diagnostic.py`

**Step 1: Write failing tests**

Add to `tests/test_memory_monitor.py`:

```python
async def test_killed_containers_cleared_on_normal_recovery(
    memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
):
    """Killed containers list should be cleared when state returns to NORMAL."""
    monitor = MemoryMonitor(
        docker_client=mock_docker_client,
        config=memory_config,
        on_alert=mock_on_alert,
        on_ask_restart=mock_on_ask_restart,
    )

    # Simulate state: was CRITICAL, killed a container, now recovering
    monitor._state = MemoryState.WARNING
    monitor._killed_containers = ["bitmagnet"]

    # Memory drops below warning threshold -> should go NORMAL
    with patch("src.monitors.memory_monitor.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = MagicMock(percent=70.0)
        await monitor._check_memory()

    assert monitor._state == MemoryState.NORMAL
    assert monitor._killed_containers == []
```

Add to `tests/test_diagnostic.py`:

```python
def test_has_pending_cleans_stale_entries():
    """has_pending() should clean up stale contexts."""
    from src.services.diagnostic import DiagnosticService, DiagnosticContext
    from datetime import datetime, timedelta

    service = DiagnosticService(anthropic_client=None)

    # Store a context that's already expired
    old_context = DiagnosticContext(
        container_name="plex", logs="error log", exit_code=1,
        image="plex:latest", uptime_seconds=100, restart_count=3,
    )
    old_context.created_at = datetime.now() - timedelta(seconds=700)
    service._pending[42] = old_context

    # has_pending should detect it's stale and remove it
    assert service.has_pending(42) is False
    assert 42 not in service._pending
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_monitor.py::test_killed_containers_cleared_on_normal_recovery tests/test_diagnostic.py::test_has_pending_cleans_stale_entries -v`
Expected: Memory monitor test FAIL (killed_containers not cleared). Diagnostic test may PASS (has_pending already checks staleness per-user — verify).

**Step 3: Implement fixes**

**3a. Memory monitor** — In `src/monitors/memory_monitor.py`, in `_check_memory()`, where state transitions to NORMAL (lines 114-116), clear the killed list:

```python
elif percent < self._config.warning_threshold:
    self._state = MemoryState.NORMAL
    self._killed_containers.clear()
    logger.info("Memory returned to normal levels")
```

**3b. System monitor** — In `src/unraid/monitors/system_monitor.py`, add cleanup in `check_once()` after line 119:

```python
# Clean stale alert cooldowns (keys older than 2x cooldown)
stale_cutoff = time.monotonic() - (_ALERT_COOLDOWN * 2)
stale_keys = [k for k, t in self._last_alert_times.items() if t < stale_cutoff]
for k in stale_keys:
    del self._last_alert_times[k]
```

**3c. Diagnostic service** — `has_pending()` already cleans per-user at lines 207-212. Add cleanup call to `get_context()` too. In `src/services/diagnostic.py`, add `self._cleanup_stale()` at the start of `get_details()` (before line 225):

```python
async def get_details(self, user_id: int) -> str | None:
    self._cleanup_stale()
    if not self.has_pending(user_id):
        return None
    ...
```

**Step 4: Run tests**

Run: `pytest tests/test_memory_monitor.py tests/test_diagnostic.py tests/test_unraid_system_monitor.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/monitors/memory_monitor.py src/unraid/monitors/system_monitor.py src/services/diagnostic.py tests/
git commit -m "fix: prevent memory leaks in monitor state tracking and diagnostic contexts"
```

---

### Task 8: Mute Manager — Defer Save During Cleanup

**Files:**
- Modify: `src/alerts/base_mute_manager.py:89-97`
- Test: `tests/test_base_mute_manager.py`

**Step 1: Write the failing test**

Add to `tests/test_base_mute_manager.py`:

```python
def test_clean_expired_does_not_save_immediately(self, tmp_path):
    """Automated cleanup should not trigger immediate disk write."""
    json_file = tmp_path / "mutes.json"
    manager = ConcreteMuteManager(json_path=str(json_file))

    # Add a mute that's already expired
    manager._mutes["expired"] = datetime.now() - timedelta(hours=1)
    manager._save()  # Save it to disk first

    # Track save calls
    import unittest.mock
    with unittest.mock.patch.object(manager, '_save') as mock_save:
        manager.get_active_mutes()  # Triggers _clean_expired

        # _clean_expired should NOT call _save
        mock_save.assert_not_called()

    # But the mute should be removed from memory
    assert "expired" not in manager._mutes
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_base_mute_manager.py::TestBaseMuteManagerPersistence::test_clean_expired_does_not_save_immediately -v`
Expected: FAIL — `_clean_expired` currently calls `_save()`.

**Step 3: Implement the fix**

In `src/alerts/base_mute_manager.py`, modify `_clean_expired()` to set a dirty flag instead of saving:

Add `self._dirty = False` to `__init__` (after line 28).

Modify `_clean_expired()`:

```python
def _clean_expired(self) -> None:
    """Remove expired mutes from memory. Does not save to disk immediately."""
    with self._lock:
        now = datetime.now()
        expired = [key for key, exp in self._mutes.items() if now >= exp]
        for key in expired:
            del self._mutes[key]
        if expired:
            self._dirty = True
```

Add a `flush()` method:

```python
def flush(self) -> None:
    """Persist deferred changes to disk if dirty."""
    with self._lock:
        if self._dirty:
            self._save()
            self._dirty = False
```

The `_add_mute()` and `_remove_mute()` methods already call `_save()` directly — these are user-initiated and should persist immediately. Also reset dirty flag in `_save()`:

In `_save()`, add at the end (before the except):
```python
self._dirty = False
```

**Step 4: Run tests**

Run: `pytest tests/test_base_mute_manager.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/alerts/base_mute_manager.py tests/test_base_mute_manager.py
git commit -m "perf: defer disk writes during automated mute cleanup"
```

---

### Task 9: Docker Events Reconnect — Cache Container List

**Files:**
- Modify: `src/monitors/docker_events.py:149-164, 212-230`
- Test: `tests/test_docker_events.py`

**Step 1: Write the failing test**

Add to `tests/test_docker_events.py`:

```python
def test_reconnect_calls_containers_list_once():
    """Reconnect should fetch container list once, not twice."""
    from unittest.mock import MagicMock, patch, call
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
    )

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.attrs = {"State": {"Health": {}}}
    mock_client.containers.list.return_value = [mock_container]
    monitor._client = mock_client

    monitor._reconnect()

    # containers.list should be called exactly once (not twice)
    assert mock_client.containers.list.call_count == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_docker_events.py::test_reconnect_calls_containers_list_once -v`
Expected: FAIL — currently calls `containers.list(all=True)` twice (line 224 + line 158 via `load_initial_state`).

**Step 3: Implement the fix**

Modify `load_initial_state()` to accept an optional pre-fetched list:

```python
def load_initial_state(self, containers: list | None = None) -> None:
    """Load all containers into state manager."""
    if not self._client:
        raise RuntimeError("Not connected to Docker")

    if containers is None:
        containers = self._client.containers.list(all=True)
    for container in containers:
        if container.name not in self.ignored_containers:
            info = parse_container(container)
            self.state_manager.update(info)

    logger.info(f"Loaded {len(containers)} containers into state")
```

Modify `_reconnect()` to pass the list:

```python
def _reconnect(self) -> None:
    """Attempt to reconnect to Docker daemon."""
    if self._client:
        try:
            self._client.close()
        except Exception as e:
            logger.debug(f"Error closing Docker client during reconnect: {e}")

    self._client = docker.DockerClient(base_url=self._docker_socket_path)

    # Fetch container list once and reuse
    containers = self._client.containers.list(all=True)

    # Clear stale state
    current_names = {c.name for c in containers}
    for name in list(self.state_manager.get_all_names()):
        if name not in current_names:
            self.state_manager.remove(name)

    self.load_initial_state(containers=containers)
    logger.info("Docker reconnection successful")
```

**Step 4: Run tests**

Run: `pytest tests/test_docker_events.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/monitors/docker_events.py tests/test_docker_events.py
git commit -m "perf: eliminate duplicate container list fetch during Docker reconnect"
```

---

### Task 10: Memory Monitor — Cache psutil and Container List

**Files:**
- Modify: `src/monitors/memory_monitor.py:66-84, 153-198`
- Test: `tests/test_memory_monitor.py`

**Step 1: Implement the fixes**

**10a. Cache psutil result** — In `_execute_kill_countdown()`, store the result in a local variable:

```python
# Line 185-187: Cache psutil result
percent = self.get_memory_percent()
if percent >= self._config.critical_threshold:
    await self._stop_container(container_name)
    await self._on_alert(
        "Container Stopped",
        f"Stopped {container_name} to free memory. Memory now at {percent:.0f}%",
        "info",
        [],
    )
```

This is already nearly there — just remove the second `self.get_memory_percent()` call on what was line 187. After `_stop_container`, the memory reading from line 185 is sufficient for the alert message (the stop just happened, a fresh reading adds negligible value but costs a system call).

**10b. Pass container list** — Modify `_get_next_killable()` to accept optional list:

```python
def _get_next_killable(self, running_names: set[str] | None = None) -> str | None:
    """Get the next container to kill from the killable list."""
    if running_names is None:
        running_names = {c.name for c in self._docker.containers.list()}

    for name in self._config.killable_containers:
        if name in self._killed_containers:
            continue
        if name in running_names:
            return name

    return None
```

In `_handle_critical()`, pass running names from `_check_memory()` context. However, since `_handle_critical` doesn't have the list, and fetching it once per check cycle is fine, we can leave the current call pattern. The main optimization here is the psutil caching.

**Step 2: Run tests**

Run: `pytest tests/test_memory_monitor.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add src/monitors/memory_monitor.py
git commit -m "perf: cache psutil result and accept pre-fetched container list in memory monitor"
```

---

### Task 11: System Monitor Metric Caching

**Files:**
- Modify: `src/unraid/monitors/system_monitor.py:42, 68-153`
- Test: `tests/test_unraid_system_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_unraid_system_monitor.py`:

```python
async def test_get_current_metrics_returns_cached():
    """get_current_metrics should return cached data from last check_once()."""
    from unittest.mock import AsyncMock, MagicMock
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor

    mock_client = AsyncMock()
    mock_config = MagicMock()
    mock_config.poll_system_seconds = 30
    mock_config.cpu_temp_threshold = 80
    mock_config.cpu_usage_threshold = 90
    mock_config.memory_usage_threshold = 90
    mock_mute = MagicMock()
    mock_mute.is_server_muted.return_value = False

    monitor = UnraidSystemMonitor(
        client=mock_client,
        config=mock_config,
        on_alert=AsyncMock(),
        mute_manager=mock_mute,
    )

    metrics = {"cpu_percent": 50.0, "cpu_temperature": 60.0, "memory_percent": 40.0}
    mock_client.get_system_metrics.return_value = metrics

    # Run check_once to populate cache
    await monitor.check_once()

    # get_current_metrics should return cached data without new API call
    mock_client.get_system_metrics.reset_mock()
    result = await monitor.get_current_metrics()

    assert result == metrics
    mock_client.get_system_metrics.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_unraid_system_monitor.py::test_get_current_metrics_returns_cached -v`
Expected: FAIL — currently makes a fresh API call.

**Step 3: Implement the fix**

In `src/unraid/monitors/system_monitor.py`, add caching:

Add to `__init__`:
```python
self._cached_metrics: dict | None = None
self._cached_array: dict | None = None
self._cache_time: float = 0.0
```

Add a cache staleness constant:
```python
_CACHE_TTL = 30  # seconds — matches poll interval
```

Modify `check_once()` to cache results (after line 75):
```python
async def check_once(self) -> dict | None:
    try:
        metrics = await self._client.get_system_metrics()
    except Exception as e:
        logger.error(f"Failed to get system metrics: {e}")
        return None

    self._cached_metrics = metrics
    self._cache_time = time.monotonic()
    # ... rest of method unchanged
```

Modify `get_current_metrics()` to use cache:
```python
async def get_current_metrics(self) -> dict | None:
    """Get current metrics, using cache if fresh."""
    if self._cached_metrics and (time.monotonic() - self._cache_time) < _CACHE_TTL:
        return self._cached_metrics
    try:
        return await self._client.get_system_metrics()
    except Exception as e:
        logger.error(f"Failed to get system metrics: {e}")
        return None
```

Modify `get_array_status()` similarly:
```python
async def get_array_status(self) -> dict | None:
    """Get array status, using cache if fresh."""
    if self._cached_array and (time.monotonic() - self._cache_time) < _CACHE_TTL:
        return self._cached_array
    try:
        return await self._client.get_array_status()
    except Exception as e:
        logger.error(f"Failed to get array status: {e}")
        return None
```

Populate `_cached_array` in `check_once()` if the array monitor is separate — actually, looking at the code, `check_once()` only fetches system metrics, not array status. The array is fetched on-demand by commands. So cache `_cached_array` when `get_array_status()` is called directly:

```python
async def get_array_status(self) -> dict | None:
    if self._cached_array and (time.monotonic() - self._cache_time) < _CACHE_TTL:
        return self._cached_array
    try:
        result = await self._client.get_array_status()
        self._cached_array = result
        self._cache_time = time.monotonic()
        return result
    except Exception as e:
        logger.error(f"Failed to get array status: {e}")
        return None
```

**Step 4: Run tests**

Run: `pytest tests/test_unraid_system_monitor.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/unraid/monitors/system_monitor.py tests/test_unraid_system_monitor.py
git commit -m "perf: cache Unraid system metrics and array status with TTL"
```

---

### Task 12: Parallelize Unraid Command API Calls

**Files:**
- Modify: `src/bot/unraid_commands.py:83-143`
- Test: `tests/test_unraid_commands.py`

**Step 1: Implement the fix**

In `src/bot/unraid_commands.py`, modify `format_server_detailed()`:

```python
async def format_server_detailed(system_monitor: "UnraidSystemMonitor") -> str | None:
    """Format detailed server status."""
    # Fetch metrics and array status in parallel
    metrics, array = await asyncio.gather(
        system_monitor.get_current_metrics(),
        system_monitor.get_array_status(),
    )

    if not metrics:
        return None

    # ... rest of formatting unchanged, just remove the second await
```

Add `import asyncio` at top of file if not present.

Remove the old sequential `array = await system_monitor.get_array_status()` call (line 114).

**Step 2: Run tests**

Run: `pytest tests/test_unraid_commands.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add src/bot/unraid_commands.py
git commit -m "perf: parallelize metrics and array status fetches in /server detailed"
```

---

### Task 13: Resource Monitor — Backpressure and Periodic Cleanup

**Files:**
- Modify: `src/monitors/resource_monitor.py:161-188, 366-385`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
async def test_get_all_stats_limits_concurrency():
    """get_all_stats should limit concurrent Docker API calls."""
    import asyncio
    from unittest.mock import MagicMock, AsyncMock, patch
    from src.monitors.resource_monitor import ResourceMonitor

    # Track concurrent calls
    max_concurrent = 0
    current_concurrent = 0

    original_to_thread = asyncio.to_thread

    async def tracking_to_thread(func, *args, **kwargs):
        nonlocal max_concurrent, current_concurrent
        current_concurrent += 1
        max_concurrent = max(max_concurrent, current_concurrent)
        await asyncio.sleep(0.01)  # Simulate work
        current_concurrent -= 1
        return {"cpu_stats": {}, "precpu_stats": {}, "memory_stats": {"limit": 1}}

    # Create 20 mock containers
    containers = []
    for i in range(20):
        c = MagicMock()
        c.name = f"container_{i}"
        c.status = "running"
        c.stats = MagicMock()
        containers.append(c)

    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = containers

    monitor = ResourceMonitor(
        docker_client=mock_docker,
        config=MagicMock(enabled=True),
        on_alert=AsyncMock(),
        mute_manager=MagicMock(),
    )

    with patch("asyncio.to_thread", tracking_to_thread):
        await monitor.get_all_stats()

    assert max_concurrent <= 10, f"Too many concurrent calls: {max_concurrent}"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_get_all_stats_limits_concurrency -v`
Expected: FAIL — currently no concurrency limit.

**Step 3: Implement the fixes**

In `src/monitors/resource_monitor.py`:

Add semaphore to `__init__`:
```python
self._stats_semaphore = asyncio.Semaphore(10)
```

Add `import time` at top.

Add cleanup tracking to `__init__`:
```python
self._last_cleanup: float = 0.0
```

Modify `get_all_stats()`:
```python
async def get_all_stats(self) -> list[ContainerStats]:
    import asyncio

    containers = self._docker.containers.list(filters={"status": "running"})

    async def fetch_one(container) -> ContainerStats | None:
        async with self._stats_semaphore:
            try:
                raw_stats = await asyncio.to_thread(container.stats, stream=False)
                return parse_container_stats(container.name, raw_stats)
            except Exception as e:
                logger.warning(f"Failed to get stats for {container.name}: {e}")
                return None

    results = await asyncio.gather(*(fetch_one(c) for c in containers))
    return [s for s in results if s is not None]
```

Modify `_poll_cycle()` — move cleanup to periodic:
```python
async def _poll_cycle(self) -> None:
    """Execute one polling cycle."""
    # Cleanup stale rate limiter entries every 5 minutes
    now = time.monotonic()
    if now - self._last_cleanup > 300:
        self._last_cleanup = now
        self._rate_limiter.cleanup_stale()

    stats_list = await self.get_all_stats()
    # ... rest unchanged
```

**Step 4: Run tests**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "perf: add semaphore backpressure and periodic cleanup to resource monitor"
```

---

### Task 14: NL Processor — Enable Prompt Caching

**Files:**
- Modify: `src/services/nl_processor.py:100-121, 248-298`
- Test: `tests/test_nl_processor.py`

**Step 1: Implement the fix**

In `src/services/nl_processor.py`, modify the system prompt to use structured content with cache_control:

Modify `_call_claude()` to use cache-enabled system prompt:

```python
async def _call_claude(self, messages: list[dict]) -> tuple[str, dict | None]:
    assert self._anthropic is not None
    tools = self._get_cached_tools()
    pending_action = None

    # Use structured system prompt with cache_control
    system_with_cache = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    # Add cache_control to last tool definition
    cached_tools = tools.copy()
    if cached_tools:
        cached_tools[-1] = {
            **cached_tools[-1],
            "cache_control": {"type": "ephemeral"},
        }

    # Initial API call
    response = await self._anthropic.messages.create(
        model=self._model,
        max_tokens=self._max_tokens,
        system=system_with_cache,
        tools=cached_tools,
        messages=messages,
    )

    # Handle tool use loop
    iterations = 0
    while response.stop_reason == "tool_use" and iterations < self._max_tool_iterations:
        iterations += 1
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input

                result = await self._executor.execute(tool_name, tool_input)

                if result.startswith("CONFIRMATION_NEEDED:"):
                    _, action, container = result.split(":", 2)
                    pending_action = {"action": action, "container": container}
                    result = f"Confirmation needed to {action} {container}."

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ]

        response = await self._anthropic.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_with_cache,
            tools=cached_tools,
            messages=messages,
        )

    if iterations >= self._max_tool_iterations:
        logger.warning("Max tool iterations reached")

    text_parts = [block.text for block in response.content if block.type == "text"]
    response_text = "\n".join(text_parts) if text_parts else "I couldn't generate a response."

    return response_text, pending_action
```

**Step 2: Run tests**

Run: `pytest tests/test_nl_processor.py -v`
Expected: All PASS. The mock API calls won't validate cache_control, but the structure should not break any existing test.

**Step 3: Run full suite**

Run: `pytest tests/ -x -q`
Expected: All PASS

**Step 4: Commit**

```bash
git add src/services/nl_processor.py
git commit -m "perf: enable Anthropic prompt caching for NL processor system prompt and tools"
```

---

### Task 15: Diagnostic Service — Enable Prompt Caching

**Files:**
- Modify: `src/services/diagnostic.py:147-172, 238-260`
- Test: `tests/test_diagnostic.py`

**Step 1: Implement the fix**

In `src/services/diagnostic.py`, modify `analyze()` to use cache-enabled content blocks:

```python
async def analyze(self, context: DiagnosticContext) -> str:
    if not self._anthropic:
        return "❌ Anthropic API not configured. Set ANTHROPIC_API_KEY in .env"

    uptime_str = format_uptime(context.uptime_seconds) if context.uptime_seconds else "unknown"

    safe_name = sanitize_container_name(context.container_name)
    safe_image = sanitize_container_name(context.image)
    safe_logs = sanitize_logs(context.logs)

    prompt = f"""You are a Docker container diagnostics assistant. Analyze this container issue and provide a brief, actionable summary.

Container: {safe_name}
Image: {safe_image}
Exit Code: {context.exit_code}
Uptime before exit: {uptime_str}
Restart Count: {context.restart_count}

Last log lines:
```
{safe_logs}
```

Respond with 2-3 sentences: What happened, the likely cause, and how to fix it. Be specific and actionable. If you see a clear command to run, include it."""

    try:
        message = await self._anthropic.messages.create(
            model=self._model,
            max_tokens=self._brief_max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }],
        )
        return message.content[0].text
    except Exception as e:
        error_result = handle_anthropic_error(e)
        logger.log(error_result.log_level, f"Claude API error in analyze: {e}")
        return f"❌ {error_result.user_message}"
```

Similarly for `get_details()`.

**Step 2: Run tests**

Run: `pytest tests/test_diagnostic.py tests/test_diagnose_command.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add src/services/diagnostic.py
git commit -m "perf: enable Anthropic prompt caching for diagnostic service"
```

---

### Task 16: Pattern Analyzer — Cache Results

**Files:**
- Modify: `src/analysis/pattern_analyzer.py`
- Test: `tests/test_pattern_analyzer.py`

**Step 1: Write the failing test**

Add to `tests/test_pattern_analyzer.py`:

```python
async def test_analyze_error_caches_results():
    """Same error should return cached result without second API call."""
    from unittest.mock import AsyncMock, MagicMock
    from src.analysis.pattern_analyzer import PatternAnalyzer

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"pattern": "error.*timeout", "match_type": "regex", "explanation": "Timeout errors"}')]
    mock_client.messages.create.return_value = mock_response

    analyzer = PatternAnalyzer(anthropic_client=mock_client)

    # First call
    result1 = await analyzer.analyze_error("plex", "Connection timeout error", [])
    # Second call with same args
    result2 = await analyzer.analyze_error("plex", "Connection timeout error", [])

    assert result1 == result2
    # Should only have called API once
    assert mock_client.messages.create.call_count == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_pattern_analyzer.py::test_analyze_error_caches_results -v`
Expected: FAIL — currently makes API call every time.

**Step 3: Implement the fix**

In `src/analysis/pattern_analyzer.py`, add a TTL cache:

```python
import hashlib
import time

class PatternAnalyzer:
    _CACHE_TTL = 3600  # 1 hour

    def __init__(self, ...):
        ...
        self._cache: dict[str, tuple[float, dict]] = {}  # {key: (timestamp, result)}

    async def analyze_error(self, container, error_message, recent_logs):
        if self._client is None:
            return None

        # Check cache
        cache_key = hashlib.md5(f"{container}:{error_message}".encode()).hexdigest()
        cached = self._cache.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < self._CACHE_TTL:
            return cached[1]

        # ... existing API call logic ...

        # Cache the result (before return)
        if result is not None:
            self._cache[cache_key] = (time.monotonic(), result)

        return result
```

**Step 4: Run tests**

Run: `pytest tests/test_pattern_analyzer.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/analysis/pattern_analyzer.py tests/test_pattern_analyzer.py
git commit -m "perf: cache pattern analyzer results for 1 hour to avoid duplicate API calls"
```

---

### Task 17: Ignore Manager — Batch Save Support

**Files:**
- Modify: `src/alerts/ignore_manager.py:156-174`
- Test: `tests/test_ignore_manager.py`

**Step 1: Implement the fix**

In `src/alerts/ignore_manager.py`, add a `_defer_save` flag and context manager:

```python
from contextlib import contextmanager

class IgnoreManager:
    def __init__(self, ...):
        ...
        self._defer_save = False

    @contextmanager
    def batch_updates(self):
        """Context manager to defer saves during bulk operations."""
        self._defer_save = True
        try:
            yield
        finally:
            self._defer_save = False
            self._save_runtime_ignores()

    def add_ignore_pattern(self, container, pattern, match_type="substring", explanation=None):
        # ... existing validation ...

        with self._lock:
            # ... existing duplicate check and append ...
            self._runtime_ignores[container].append(ignore_pattern)
            if not self._defer_save:
                self._save_runtime_ignores()
            logger.info(f"Added ignore for {container}: {pattern} ({match_type})")
            return True, "Pattern added"
```

**Step 2: Run tests**

Run: `pytest tests/test_ignore_manager.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add src/alerts/ignore_manager.py
git commit -m "perf: add batch_updates context manager to defer ignore manager saves"
```

---

### Task 18: Run Full Test Suite and Final Verification

**Step 1: Run complete test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All PASS, no regressions.

**Step 2: Run type checking**

Run: `mypy src/`
Expected: No new errors.

**Step 3: Run linting**

Run: `ruff check src/`
Expected: No new errors.

**Step 4: Final commit with version bump (if all passes)**

Update version in relevant files if needed, then:

```bash
git log --oneline -20  # Review all commits from this session
```

---

## Summary

| Task | Category | Files Modified | Risk |
|------|----------|---------------|------|
| 1 | Config caching | config.py | Low |
| 2 | Deque rate limiter | utils/rate_limiter.py | Low |
| 3 | Deque conversation memory | nl_processor.py | Low |
| 4 | Fix pattern cache key | log_watcher.py | Low |
| 5 | Throttle confirmation cleanup | confirmation.py | Low |
| 6 | Auto-cleanup alert rate limiter | alerts/rate_limiter.py | Low |
| 7 | Memory leak fixes (3 items) | memory_monitor, system_monitor, diagnostic | Low |
| 8 | Defer mute manager saves | base_mute_manager.py | Medium |
| 9 | Cache container list on reconnect | docker_events.py | Low |
| 10 | Cache psutil + container list | memory_monitor.py | Low |
| 11 | System monitor metric caching | system_monitor.py | Medium |
| 12 | Parallelize Unraid API calls | unraid_commands.py | Low |
| 13 | Resource monitor backpressure | resource_monitor.py | Low |
| 14 | NL processor prompt caching | nl_processor.py | Low |
| 15 | Diagnostic prompt caching | diagnostic.py | Low |
| 16 | Pattern analyzer result caching | pattern_analyzer.py | Low |
| 17 | Ignore manager batch saves | ignore_manager.py | Low |
| 18 | Final verification | — | — |
