# Optimization Pass Design - UnraidMonitor v0.8.2

**Date:** 2026-02-15
**Scope:** Tiers 1-2 (22 items across API cost, performance, resource efficiency)

---

## 1. API Cost Reduction

### 1A. Enable Prompt Caching in NL Processor
**File:** `src/services/nl_processor.py`
**Problem:** The Anthropic API requires `system` and `tools` on every request. In multi-tool conversations (up to 5 iterations), the static system prompt (~500 tokens) and tool definitions are re-processed each time.
**Fix:** Add `cache_control: {"type": "ephemeral"}` to the system prompt and tools list. This enables Anthropic's prompt caching so subsequent calls in the same tool loop pay ~10% of the input token cost for cached content.

### 1B. Cache Pattern Analyzer Results
**File:** `src/bot/ignore_command.py`
**Problem:** `pattern_analyzer.analyze_error()` is called both when a user selects an error in `/ignore` and when they click "Ignore Similar" on an alert. Same error = same API call twice.
**Fix:** Add an LRU cache (keyed on `container + error_message_hash`) with 1-hour TTL to `PatternAnalyzer`. Return cached result on second call.

### 1C. Enable Prompt Caching in Diagnostic Service
**File:** `src/services/diagnostic.py`
**Problem:** When a user runs `/diagnose` then requests details, the logs are re-sent as a fresh API call. Same logs could benefit from prompt caching.
**Fix:** Add `cache_control` to the log content block in the messages. Store the conversation as a multi-turn exchange so the detailed follow-up builds on the cached brief analysis.

---

## 2. Config Property Caching
**File:** `src/config.py`
**Problem:** Every access to `config.ai`, `config.bot`, `config.docker`, etc. calls `from_dict()` creating a new dataclass instance. If code accesses `config.ai` 10 times in a request, it parses 10 times.
**Fix:** Cache config objects as instance attributes in `__init__`:
```python
def __init__(self, settings: Settings):
    self._settings = settings
    self._yaml_config = load_yaml_config(settings.config_path)
    self._ai = AIConfig.from_dict(self._yaml_config.get("ai", {}))
    self._bot = BotConfig.from_dict(self._yaml_config.get("bot", {}))
    # ... etc

@property
def ai(self) -> AIConfig:
    return self._ai
```

---

## 3. Memory Leak Prevention

### 3A. Auto-Cleanup for Alert Rate Limiter
**File:** `src/alerts/rate_limiter.py`
**Problem:** `cleanup_stale()` exists but is never called automatically. Dicts grow unbounded over days.
**Fix:** Call `cleanup_stale()` inside `should_alert()` every N calls (e.g., every 100th call using a counter), or add a `_last_cleanup` timestamp and clean every hour.

### 3B. System Monitor Alert Cooldown Cleanup
**File:** `src/unraid/monitors/system_monitor.py`
**Problem:** `_last_alert_times` dict grows unbounded with unique alert keys.
**Fix:** Add periodic cleanup - remove entries older than 2x the cooldown period during `check_once()`.

### 3C. Memory Monitor Killed Containers Cleanup
**File:** `src/monitors/memory_monitor.py`
**Problem:** `_killed_containers` list grows unbounded. Never cleared on NORMAL state recovery.
**Fix:** Clear `_killed_containers` when state transitions back to NORMAL.

### 3D. Diagnostic Pending Contexts Background Cleanup
**File:** `src/services/diagnostic.py`
**Problem:** `_cleanup_stale()` only runs when `store_context()` is called. If users don't trigger new diagnostics, old contexts sit in memory.
**Fix:** Run cleanup in `has_pending()` and `get_context()` as well (lightweight - just a timestamp check).

---

## 4. Disk I/O Reduction

### 4A. Batch Mute Manager Persistence
**File:** `src/alerts/base_mute_manager.py`
**Problem:** `_save()` called on every `_add_mute()`, `_remove_mute()`, and `_clean_expired()`. Each save is an atomic write (tempfile + rename).
**Fix:** Add a dirty flag. Save immediately on explicit add/remove (user-initiated), but defer save during `_clean_expired()` (automated). Add a periodic flush (every 5 minutes) to persist deferred changes.

### 4B. Batch Ignore Manager Persistence
**File:** `src/alerts/ignore_manager.py`
**Problem:** `_save_runtime_ignores()` called on every `add_ignore_pattern()`.
**Fix:** Same dirty-flag pattern. User-initiated adds save immediately (important for data safety). Bulk operations (e.g., adding multiple patterns) could use a context manager that defers save until completion.

---

## 5. Redundant API Call Reduction

### 5A. Docker Events Reconnect - Cache Container List
**File:** `src/monitors/docker_events.py`
**Problem:** `_reconnect()` calls `containers.list(all=True)` at line 224, then `load_initial_state()` calls it again at line 229.
**Fix:** Pass the already-fetched container list to `load_initial_state()` via an optional parameter.

### 5B. Memory Monitor - Cache Container List
**File:** `src/monitors/memory_monitor.py`
**Problem:** `_get_next_killable()` calls `containers.list()` every 10 seconds (6/min).
**Fix:** Cache the container list for the duration of one check cycle. Pass the list from `_check_memory()` to `_get_next_killable()` instead of re-fetching.

### 5C. Memory Monitor - Cache psutil Result
**File:** `src/monitors/memory_monitor.py`
**Problem:** `get_memory_percent()` called twice in `_execute_kill_countdown()` (lines 185 and 187).
**Fix:** Cache the result in a local variable and reuse.

### 5D. System Monitor Metric Caching
**File:** `src/unraid/monitors/system_monitor.py`
**Problem:** `check_once()` and public getter methods (`get_current_metrics()`, `get_array_status()`) each make independent GraphQL calls.
**Fix:** Cache metrics in instance attributes during `check_once()`. Public getters return cached values (with staleness check).

### 5E. Parallelize Unraid Command API Calls
**File:** `src/bot/unraid_commands.py`
**Problem:** `format_server_detailed()` calls `get_current_metrics()` and `get_array_status()` sequentially.
**Fix:** Use `asyncio.gather()` to parallelize the two calls.

---

## 6. Data Structure Improvements

### 6A. Per-User Rate Limiter: List to Deque
**File:** `src/utils/rate_limiter.py`
**Problem:** O(n) list comprehension on every `is_allowed()` call creates two new lists. Also O(n) in `get_retry_after()`.
**Fix:** Replace `list[datetime]` with `collections.deque(maxlen=max_per_minute)` and `deque(maxlen=max_per_hour)`. Append is O(1), oldest auto-evicted. Filter only when checking limits (not on every call).

### 6B. Conversation Memory: List to Deque
**File:** `src/services/nl_processor.py`
**Problem:** `ConversationMemory.add_exchange()` appends then trims via list slicing `[-max_messages:]`, creating a new list.
**Fix:** Use `collections.deque(maxlen=max_exchanges * 2)`. Auto-evicts oldest on append, no slicing needed.

### 6C. Log Watcher Pattern Cache: Fix Cache Key
**File:** `src/monitors/log_watcher.py`
**Problem:** Cache key uses `id(error_patterns)` which breaks when config reloads create new list objects with identical content.
**Fix:** Use `(tuple(error_patterns), tuple(ignore_patterns))` as cache key. Content-based, not identity-based.

### 6D. Confirmation Manager: Reduce Cleanup Frequency
**File:** `src/bot/confirmation.py`
**Problem:** `_cleanup_expired()` runs O(n) scan on every `request()` call.
**Fix:** Add a `_last_cleanup` timestamp. Only run cleanup if >60 seconds since last cleanup.

---

## 7. Concurrency Fixes

### 7A. Resource Monitor Backpressure
**File:** `src/monitors/resource_monitor.py`
**Problem:** `get_all_stats()` gathers N containers in parallel with no limit. 30+ concurrent Docker API calls can cause memory spikes.
**Fix:** Add `asyncio.Semaphore(10)` to limit concurrent `container.stats()` calls.

### 7B. Resource Monitor: Move Cleanup to Periodic
**File:** `src/monitors/resource_monitor.py`
**Problem:** `cleanup_stale()` called every poll cycle (every 30s). Only needs to run periodically.
**Fix:** Track `_last_cleanup` timestamp. Run cleanup every 5 minutes instead of every cycle.

---

## Out of Scope (Tier 3 - Future Work)
- Container name resolution dedup (6+ locations)
- Markdown error handling dedup (4+ locations)
- Missing `callback.answer()` in error paths
- Unused `unraid-api` dependency removal
- Dockerfile image pinning and multi-stage builds
- Anthropic client dedup on first-run path
- Docker client cleanup on error paths
- Shutdown timeout wrapping

---

## Risk Assessment
- **Config caching (#2):** Low risk - config is read-only after startup
- **Data structure changes (#6A, #6B):** Low risk - deque is drop-in for append/iterate patterns
- **Prompt caching (#1A, #1C):** Low risk - additive change, no behavior difference
- **I/O batching (#4A, #4B):** Medium risk - must ensure user-initiated changes still persist immediately
- **Concurrency fixes (#7A):** Low risk - semaphore only limits parallelism, doesn't change logic
- **Cache fixes (#5D):** Medium risk - must handle staleness correctly
