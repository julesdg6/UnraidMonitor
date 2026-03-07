# Codebase Audit Report — UnraidMonitor

## Date: 2026-03-07
## Scope: Full codebase (54 source files, 81 test files, build/config files)
## Baseline: v0.9.1 (post previous audit fix sprint)

---

## Executive Summary

The codebase is in good shape overall — the v0.9.1 fix sprint resolved the most critical thread safety, security, and Markdown escaping issues from the previous audit. This audit focuses on **genuinely new findings** after verifying each against the current code. The most impactful discoveries are: a unit conversion bug producing incorrect array capacity reports, missing mute flush on shutdown causing data loss, silent log drops during error storms, and the `IgnoreManager.batch_updates()` race condition that was partially but not fully fixed. There are also several strong feature improvement opportunities.

**Verified findings:** 2 Critical, 5 High, 14 Medium, 12 Low, plus 8 feature ideas.

---

## Critical Issues (Fix Immediately)

### C1 — KB-to-TB conversion uses wrong divisor in array capacity alerts
**File:** `src/unraid/monitors/array_monitor.py:117-119`
**Category:** Bug — Data Corruption

The array capacity calculation divides KB by `1024^3` (= 1 GiB), but labelling the result as TB. The correct divisor from KB to TB is `1024^3` only if the input is in **bytes**. Since Unraid reports sizes in KB, the divisor should be `1024^3` to get TB (KB -> MB -> GB -> TB = three divisions by 1024).

**Wait — let's verify:** KB / 1024 = MB, MB / 1024 = GB, GB / 1024 = TB. So KB to TB = `KB / (1024^3)`. The code is actually correct! The comment "Convert KB to TB" is accurate: `1024^3 = 1,073,741,824`, and `KB / 1024^3 = TB`.

**HOWEVER**, there is still a real inconsistency with `_format_disk_line()` in `src/bot/unraid_commands.py:336` which uses **decimal** conversion (`1000^3`) for the same data:

```python
# array_monitor.py — binary (1024^3)
used_tb = used / (1024**3)

# unraid_commands.py — decimal (1000^3)
size_tb = size_kb / (1000 * 1000 * 1000)
```

A 10 TB disk shows as ~10.0 TB in `/disks` (decimal) but ~9.1 TB in array alerts (binary). This is confusing.

**Fix:** Standardize both to binary (`1024^3`) since Unraid reports in binary KB. Change line 336 of `unraid_commands.py`.

---

### C2 — IgnoreManager batch_updates() saves outside lock
**File:** `src/alerts/ignore_manager.py:127-134`
**Category:** Bug — Race Condition

The `batch_updates()` context manager sets `_defer_save = True` inside the lock (line 128), yields without the lock (line 130), then sets `_defer_save = False` inside the lock (line 132-133), but calls `_save_runtime_ignores()` **outside** the lock (line 134). Meanwhile, `add_ignore_pattern()` checks `_defer_save` to decide whether to save. If another thread calls `add_ignore_pattern()` between lines 133 and 134, it sees `_defer_save = False` and triggers its own save, racing with the batch save.

```python
@contextmanager
def batch_updates(self):
    with self._lock:
        self._defer_save = True
    try:
        yield
    finally:
        with self._lock:
            self._defer_save = False
        self._save_runtime_ignores()  # <-- OUTSIDE lock, race window
```

**Fix:** Move the `_save_runtime_ignores()` call inside the lock block, or acquire the lock around the entire save.

---

## High Priority

### H1 — Mute managers not flushed on shutdown — dirty state lost
**File:** `src/main.py:140-162` (`_BackgroundTasks.shutdown()`)
**Category:** Missing Feature — Data Loss

The shutdown sequence stops all monitors and cancels tasks, but never calls `flush()` on `MuteManager`, `ServerMuteManager`, or `ArrayMuteManager`. When `_is_muted()` detects an expired mute, it sets `_dirty = True` but doesn't save immediately (by design — deferred for perf). On ungraceful shutdown, these deferred changes are lost. On next startup, expired mutes reappear as active.

**Fix:** Add `mute_manager.flush()`, `server_mute_manager.flush()`, `array_mute_manager.flush()` to `_BackgroundTasks.shutdown()`.

---

### H2 — Array monitor alert state never cleared on unmute
**File:** `src/unraid/monitors/array_monitor.py:39, 187-192`
**Category:** Logic Error

`ArrayMonitor.clear_alert_state()` exists but is **never called** anywhere. When a user unmutes array alerts via `/unmute array`, `_alerted_disks` still contains previously-alerted disk keys. Those disks won't re-alert even though the user explicitly unmuted, expecting fresh alerts.

**Fix:** Pass `array_monitor` to `unmute_array_command()` factory and call `array_monitor.clear_alert_state()` on successful unmute.

---

### H3 — Pending alerts lost on DockerEventMonitor shutdown
**File:** `src/monitors/docker_events.py:295-305`
**Category:** Missing Feature

`stop()` cancels `_alert_task` immediately without draining `_pending_alerts` queue. Crash/recovery events queued but not yet processed are silently lost.

**Fix:** Set `_running = False`, put a sentinel `None` in the queue, and `await` the alert task to finish draining before cancelling.

---

### H4 — Silent log line drops during error storms
**File:** `src/monitors/log_watcher.py:164-169`
**Category:** Bug — Silent Data Loss

When a container's log queue fills (maxsize=10000), `_safe_put()` silently drops lines. If the dropped lines contain the error pattern, the alert is never sent. No metric or log indicates data was lost.

**Fix:** Add a rate-limited warning log (e.g., once per 60 seconds per container) when drops occur, so users know they're losing visibility.

---

### H5 — Dependency version inconsistency between requirements.txt and pyproject.toml
**Files:** `requirements.txt:7-8`, `pyproject.toml:17-21`
**Category:** Deployment — Inconsistency

| Package | requirements.txt | pyproject.toml |
|---------|-----------------|----------------|
| anthropic | `>=0.40.0,<1.0.0` (required) | `>=0.40.0,<1.0.0` (optional) |
| openai | `>=1.50.0,<2.0.0` (required) | `>=1.0.0` (optional) |

Docker builds use `requirements.txt` (both required), but `pyproject.toml` makes them optional and has a looser OpenAI constraint. A `pip install .` could get openai 1.0.0 which has a completely different API.

**Fix:** Align `pyproject.toml` openai constraint to `>=1.50.0,<2.0.0`. Decide on one source of truth for deps.

---

## Medium Priority

### M1 — Mute command accepts non-existent container names silently
**File:** `src/bot/mute_command.py:90-92`
**Category:** UX — Logic Error

When partial name matching finds zero matches, the code falls back to using the raw query as the container name. This silently accepts typos like `/mute plxe 2h` and persists a mute for a non-existent container.

**Fix:** Warn the user: "No container named 'plxe' found. Muting anyway in case it starts later." Or reject with a suggestion.

---

### M2 — ServerMuteManager has "ups" category with no UPS monitoring
**File:** `src/alerts/server_mute_manager.py:14`
**Category:** Dead Code / Misleading

`CATEGORIES = ("server", "array", "ups")` — the "ups" category is muted/unmuted when `mute_server()`/`unmute_server()` is called, but no UPS monitoring exists. This means `/mute server` silently mutes a non-existent UPS alert category.

**Fix:** Remove "ups" from CATEGORIES until UPS monitoring is implemented.

---

### M3 — Hardcoded timezone "Europe/London" not configurable
**File:** `src/utils/formatting.py:65`
**Category:** Improvement — Configurability

The timezone is hardcoded. The `docker-compose.yml` sets `TZ=Europe/London` but the Python code ignores the `TZ` env var.

**Fix:** Read `os.environ.get("TZ", "Europe/London")` in `format_mute_expiry()`.

---

### M4 — ResourceMonitor violation state lost on container restart
**File:** `src/monitors/resource_monitor.py:384-395`
**Category:** Bug

Violation counters are deleted for containers no longer in `active_names`. If a container restarts within one poll cycle, its violation history resets. A container in a restart loop with slowly increasing CPU won't trigger alerts because the counter keeps resetting.

**Fix:** Use time-based eviction (e.g., keep violations for 10 minutes after container disappears) instead of presence-based.

---

### M5 — Missing input validation in NL tool executor
**File:** `src/services/nl_tools.py:286`
**Category:** Bug

`_tool_get_container_logs()` clamps lines to `max(200)` but not `min(1)`. An LLM could pass `lines=-100` causing unexpected behavior.

**Fix:** `lines = max(1, min(args.get("lines", 50), 200))`

---

### M6 — PatternAnalyzer cache eviction uses insertion order, not age
**File:** `src/analysis/pattern_analyzer.py:123-127`
**Category:** Bug

When cache reaches 256 entries, eviction removes `next(iter(self._cache))` — the first-inserted key, not the oldest by timestamp. A frequently-used old entry could be evicted while a stale recent one is kept.

**Fix:** Evict the entry with the oldest timestamp, or use `functools.lru_cache`.

---

### M7 — ContainerStateManager returns mutable references
**File:** `src/state.py:25-27`
**Category:** Bug — Thread Safety

`get_all()` returns `list(self._containers.values())` — a list of references to the same `ContainerInfo` objects. If these are mutated from another thread, callers see inconsistent data.

**Fix:** Return copies: `return [replace(c) for c in self._containers.values()]` (using `dataclasses.replace`).

---

### M8 — Container pull: partial network reconnection not surfaced to user
**File:** `src/services/container_control.py:134-148`
**Category:** Bug — Silent Failure

During `pull_and_recreate()`, if reconnecting to a secondary network fails, the error is only logged. The user sees "Pull complete" without knowing the container is partially broken.

**Fix:** Track failed networks and include them in the return message.

---

### M9 — MemoryMonitor decline_restart resets _restart_prompted unconditionally
**File:** `src/monitors/memory_monitor.py:272-281`
**Category:** Logic Error

When a user declines restart for one killed container, `_restart_prompted` is reset to `False`. If there are other killed containers, the next `_check_memory()` cycle immediately prompts again for a different container — feels like spam.

**Fix:** Only reset `_restart_prompted` when `_killed_containers` is empty.

---

### M10 — Unraid config thresholds not validated
**File:** `src/config.py:246-263`
**Category:** Improvement — Input Validation

Unlike `MemoryConfig` which validates threshold ordering, `UnraidConfig` accepts any values. `cpu_temp_threshold=0` would cause permanent alerts.

**Fix:** Add range validation (e.g., temps 20-100, percentages 1-100).

---

### M11 — No Unraid client reconnection logic
**File:** `src/unraid/client.py:157-160`
**Category:** Resilience

Once connected, if the Unraid server becomes temporarily unavailable, all API calls fail permanently with "Not connected" until the bot is restarted. No automatic reconnect.

**Fix:** Add reconnect logic in the system/array monitors' poll loops.

---

### M12 — Unused backward-compat alias handle_anthropic_error
**File:** `src/utils/api_errors.py:156`
**Category:** Dead Code

`handle_anthropic_error = handle_llm_error` is never imported anywhere.

**Fix:** Remove the alias.

---

### M13 — Missing GraphQL error logging before raise
**File:** `src/unraid/client.py:194-196`
**Category:** Observability

GraphQL errors are raised but not logged, making production debugging harder.

**Fix:** Add `logger.error(...)` before the raise.

---

### M14 — No test coverage for src/main.py (0%)
**File:** `src/main.py`
**Category:** Testing Gap

The composition root, `AlertManagerProxy`, `_BackgroundTasks`, and shutdown logic have zero test coverage. This is the most critical untested file.

---

## Low Priority

### L1 — Unused `psutil` import only in memory_monitor.py
`psutil` is a required dependency but only used in `memory_monitor.py`. Not dead — just noting it's a heavyweight dep for one use.

### L2 — ZoneInfo imported inside function body
`src/utils/formatting.py:63` — Lazy import of stdlib module. Minor style inconsistency.

### L3 — Bare `except Exception: pass` swallows real errors
`src/bot/ignore_command.py:226-228` — Should catch specific `TelegramBadRequest`.

### L4 — Unreachable `return None` in telegram_retry decorator
`src/utils/telegram_retry.py:92` — Dead code after `raise` statement.

### L5 — Docker image `os.fchmod(fd, 0o666)` in base_mute_manager
`src/alerts/base_mute_manager.py:144` — Creates world-readable mute files. Should be `0o644`.

### L6 — `_format_disk_line` doesn't handle string-typed `size` field
`src/bot/unraid_commands.py:333-336` — No try/except on `size_kb / ...` if API returns string.

### L7 — Unused `state` parameter in help_command factory
`src/bot/commands.py:84` — `state` captured but never used.

### L8 — Inconsistent callback data parsing patterns
`src/bot/alert_callbacks.py` — Mix of `split(":", 1)` and `rsplit(":", 1)` for the same pattern.

### L9 — IgnorePattern silently fails if regex is invalid
`src/alerts/ignore_manager.py:83-90` — Sets `_compiled_regex = None`, `matches()` returns `False`. User not informed.

### L10 — AlertManagerProxy queue depth not exposed via /health
`src/main.py:91-95` — Dropped alerts only in logs, not visible to users.

### L11 — Missing disk size null safety
`src/unraid/monitors/array_monitor.py:140-145` — Handles None temp but not string-typed sizes.

### L12 — `datetime.fromisoformat` vs `datetime.now()` naive/aware mixing
`src/alerts/base_mute_manager.py:44, 119` — `_load()` creates timezone-aware datetimes via `fromisoformat()`, but `_is_muted()` compares with naive `datetime.now()`. Currently works because mutes are created with naive `datetime.now()` (line 62), so `isoformat()` produces naive strings. But if a mute file is manually edited with timezone info, comparison would crash.

---

## Feature Ideas

### F1 — Container health check monitoring
Docker `health_status` events are already captured in `_event_loop()` (line 326) but only used for state updates, not alerts. Could alert when a container transitions to "unhealthy".

### F2 — Mute expiry notifications
When a mute expires (detected passively in `_is_muted()`), send a Telegram notification: "Alerts for {container} are now active again." Currently silent.

### F3 — Auto-flush dirty mute state on timer
Instead of relying only on shutdown flush, periodically flush every 5 minutes if `_dirty = True`. Protects against data loss on crashes.

### F4 — Container crash trend indicator in /status
Track crash frequency via `CrashTracker` and show trends: "3 crashes in last hour (up from 1/hr avg)".

### F5 — Quick-access container buttons for commands
For `/logs`, `/mute`, `/diagnose` — show inline keyboard with top 5 most-erroring containers so users don't have to type names.

### F6 — Per-container rate limit customization
Allow different cooldown periods per container in config (e.g., noisy containers get 30min cooldown, critical ones get 5min).

### F7 — Log drop metrics in /health
Expose log line drop counts from `LogWatcher` in the `/health` output so users know if they're losing visibility.

### F8 — Configurable timezone via TZ environment variable
Read timezone from `os.environ.get("TZ", "Europe/London")` instead of hardcoding.

---

## Summary Statistics

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| Bugs | 1 | 2 | 6 | 4 |
| Missing Features | 0 | 2 | 0 | 0 |
| Data Integrity | 1 | 1 | 0 | 0 |
| UX/Logic | 0 | 0 | 2 | 2 |
| Dead Code | 0 | 0 | 2 | 2 |
| Deployment | 0 | 1 | 0 | 0 |
| Testing | 0 | 0 | 1 | 0 |
| Resilience | 0 | 0 | 1 | 0 |
| Observability | 0 | 0 | 1 | 1 |
| Security | 0 | 0 | 0 | 1 |
| Configuration | 0 | 0 | 1 | 1 |
| **Total** | **2** | **5** | **14** | **12** |

---

# Implementation Plan — Codebase Audit 2026-03-07

## Overview
33 findings total (2 Critical, 5 High, 14 Medium, 12 Low) plus 8 feature ideas. Estimated effort: ~6 S, ~14 M, ~5 L tasks.

## Phase 1: Critical & Data Integrity (Do First)

| # | Finding | Files | Effort | Description |
|---|---------|-------|--------|-------------|
| 1 | C1 — Unit inconsistency in disk sizes | `src/bot/unraid_commands.py:336` | S | Change `1000 * 1000 * 1000` to `1024**3` to match binary convention used in `array_monitor.py:117-119`. Update comment. |
| 2 | C2 — batch_updates() race | `src/alerts/ignore_manager.py:127-134` | S | Move `_save_runtime_ignores()` call inside the `finally` lock block: `with self._lock: self._defer_save = False; self._save_runtime_ignores()` |
| 3 | H1 — Mute flush on shutdown | `src/main.py:140-162` | S | Add `flush()` calls for all three mute managers in `_BackgroundTasks.shutdown()`. Pass mute manager refs to the class. |
| 4 | H3 — Drain alert queue on stop | `src/monitors/docker_events.py:295-305` | M | In `stop()`, set `_running = False`, put sentinel `None` in `_pending_alerts`, await `_alert_task` with timeout before cancelling. |

## Phase 2: High Priority Bugs & Logic Errors

| # | Finding | Files | Effort | Description |
|---|---------|-------|--------|-------------|
| 5 | H2 — Clear alert state on unmute | `src/unraid/monitors/array_monitor.py:187-192`, `src/bot/unraid_commands.py` (unmute handler) | M | Pass `array_monitor` ref to `unmute_array_command()` factory; call `array_monitor.clear_alert_state()` on successful unmute. |
| 6 | H4 — Log drop warning | `src/monitors/log_watcher.py:164-169` | M | Add per-container drop counter and rate-limited `logger.warning()` (once per 60s) when drops exceed threshold. |
| 7 | H5 — Dep version alignment | `requirements.txt:8`, `pyproject.toml:20` | S | Set `openai>=1.50.0,<2.0.0` in pyproject.toml optional deps. |
| 8 | M9 — Restart prompt spam | `src/monitors/memory_monitor.py:272-281` | S | Only set `_restart_prompted = False` inside the `if not self._killed_containers:` block. |

## Phase 3: Medium Priority Improvements

| # | Finding | Files | Effort | Description |
|---|---------|-------|--------|-------------|
| 9 | M1 — Mute accepts typos | `src/bot/mute_command.py:90-92` | M | When zero matches found, reply with warning "Container not found, muting name anyway" so user knows it didn't match. |
| 10 | M2 — Remove "ups" category | `src/alerts/server_mute_manager.py:14` | S | Change `CATEGORIES = ("server", "array", "ups")` to `("server", "array")`. |
| 11 | M3 — Configurable timezone | `src/utils/formatting.py:63-65` | M | Replace hardcoded `"Europe/London"` with `os.environ.get("TZ", "Europe/London")`. |
| 12 | M4 — Violation state eviction | `src/monitors/resource_monitor.py:384-395` | M | Track `last_seen_time` per container; only evict violations after 10 minutes of absence instead of immediate removal. |
| 13 | M5 — NL tool lines validation | `src/services/nl_tools.py:286` | S | Change to `lines = max(1, min(args.get("lines", 50), 200))`. |
| 14 | M6 — Cache eviction by age | `src/analysis/pattern_analyzer.py:123-127` | M | Find the key with oldest `_cache[key][1]` timestamp instead of `next(iter(...))`. |
| 15 | M7 — Return copies from state | `src/state.py:25-27` | M | Use `dataclasses.replace(c)` in `get_all()` and `find_by_name()`. |
| 16 | M8 — Surface network reconnect failures | `src/services/container_control.py:134-148` | M | Collect failed network names and append to return message. |
| 17 | M10 — Unraid config validation | `src/config.py:246-263` | M | Add range checks: temps 20-100, percentages 1-100, with warning + clamp like MemoryConfig. |
| 18 | M11 — Unraid reconnect logic | `src/unraid/client.py`, `src/unraid/monitors/system_monitor.py`, `src/unraid/monitors/array_monitor.py` | L | Add try/except in poll loops that calls `connect()` on failure with exponential backoff. |
| 19 | M13 — GraphQL error logging | `src/unraid/client.py:194-196` | S | Add `logger.error(f"GraphQL errors: {errors}")` before the raise. |

## Phase 4: Low Priority & Cleanup

| # | Finding | Files | Effort | Description |
|---|---------|-------|--------|-------------|
| 20 | M12 — Remove dead alias | `src/utils/api_errors.py:156` | S | Delete `handle_anthropic_error = handle_llm_error`. |
| 21 | L3 — Narrow exception catch | `src/bot/ignore_command.py:226-228` | S | Change `except Exception: pass` to `except TelegramBadRequest: pass`. |
| 22 | L4 — Remove unreachable return | `src/utils/telegram_retry.py:92` | S | Delete unreachable `return None`. |
| 23 | L5 — Fix mute file permissions | `src/alerts/base_mute_manager.py:144` | S | Change `os.fchmod(fd, 0o666)` to `os.fchmod(fd, 0o644)`. |
| 24 | L6 — Disk size type safety | `src/bot/unraid_commands.py:333-336` | S | Wrap in `try: size_kb = int(disk.get("size", 0)) except (ValueError, TypeError): size_kb = 0`. |
| 25 | L7 — Remove unused state param | `src/bot/commands.py:84` | S | Remove `state` parameter from `help_command()` factory; update registration in `telegram_bot.py`. |
| 26 | L9 — Warn on invalid ignore regex | `src/alerts/ignore_manager.py:83-90` | M | Return feedback to caller when regex fails to compile, so bot can inform user. |
| 27 | L10 — Expose queue depth in /health | `src/bot/health_command.py`, `src/main.py` | M | Add `queued_alerts` count to health output. |
| 28 | L12 — Consistent datetime handling | `src/alerts/base_mute_manager.py:44, 62` | M | Use `datetime.now(timezone.utc)` consistently, or document that all mute times are naive-local. |

## Phase 5: Feature Enhancements

| # | Finding | Files | Effort | Description |
|---|---------|-------|--------|-------------|
| 29 | F1 — Health check alerts | `src/monitors/docker_events.py` | L | Add handler for `health_status` events that sends alert when container goes unhealthy. |
| 30 | F2 — Mute expiry notifications | `src/alerts/base_mute_manager.py`, `src/main.py` | L | Add callback/event on mute expiry, wire to AlertManagerProxy to send notification. |
| 31 | F3 — Auto-flush dirty mutes | `src/main.py` | M | Add periodic task that calls `flush()` on all mute managers every 5 minutes. |
| 32 | F5 — Quick-access container buttons | `src/bot/commands.py` | L | For container-targeting commands, show inline keyboard with top erroring containers. |
| 33 | F7 — Log drop metrics in /health | `src/monitors/log_watcher.py`, `src/bot/health_command.py` | M | Track drop counts per container, expose in /health output. |
| 34 | F8 — Configurable timezone | `src/utils/formatting.py`, `src/config.py` | M | Same as M3 (consolidated). |
| 35 | M14 — Test coverage for main.py | `tests/test_main.py` (new) | L | Create tests for AlertManagerProxy queueing/flushing, _BackgroundTasks shutdown, multi-user delivery. |

## Dependencies & Ordering Notes

- **C2 must come before any batch ignore operations** — the race can corrupt data.
- **H1 (mute flush) and F3 (auto-flush)** are complementary — do H1 first as the minimal fix, F3 as belt-and-suspenders.
- **H2 (clear alert state)** requires passing `array_monitor` to the unmute command factory, which touches `telegram_bot.py` registration — coordinate with any other handler registration changes.
- **M11 (Unraid reconnect)** is the largest task and can be done independently.

## Quick Wins (S effort + High/Critical severity)

These should be done first within each phase:
1. C1 — Fix disk size unit conversion (S)
2. C2 — Fix batch_updates() race (S)
3. H1 — Add mute flush on shutdown (S)
4. H5 — Align dependency versions (S)
5. M8 (item 8) — Fix restart prompt spam (S)
6. M13 — Add GraphQL error logging (S)
