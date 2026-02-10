# Codebase Deep Audit — 2026-02-10

Comprehensive audit of the Unraid Monitor Bot codebase covering bugs, race conditions,
performance bottlenecks, error handling gaps, missing features, and test quality.

---

## Critical Bugs (Fix First)

### C1. Blocking Docker calls freeze the bot
- **File:** `src/bot/alert_callbacks.py:139-141`
- **Issue:** `logs_callback` calls `docker_client.containers.get()` and `container.logs()` synchronously on the event loop. Every other handler correctly uses `await asyncio.to_thread()`.
- **Impact:** Entire Telegram bot freezes for seconds when a user taps "View Logs" on an alert.
- **Fix:** Wrap both calls in `await asyncio.to_thread()` as done in `commands.py`.

### C2. Container rollback crashes when image was not found
- **File:** `src/services/container_control.py:87,120`
- **Issue:** In `pull_and_recreate`, if the original image wasn't found (`old_image_id = None`), the rollback path calls `containers.run(None, ...)`, which crashes.
- **Impact:** User's container is deleted with no recovery possible.
- **Fix:** Check `old_image_id is not None` before rollback. Use `image_name` string as fallback.

### C3. Multi-network containers lose secondary networks on recreate
- **File:** `src/services/container_control.py:148-238`
- **Issue:** `_extract_run_config` only preserves `NetworkMode` from `HostConfig`, not the full `EndpointsConfig` from `NetworkSettings.Networks`.
- **Impact:** Containers connected to multiple Docker networks lose all secondary connections after `/pull`.
- **Fix:** Extract and apply `networking_config` from `NetworkSettings.Networks` to reconnect all networks after creation.

### C4. `\w+` regex truncates hyphenated container names
- **Files:** `src/utils/formatting.py:6-11`, `src/bot/diagnose_command.py:16`
- **Issue:** Container name extraction uses `\w+` which doesn't match hyphens or dots (e.g., `my-app`, `app.v2`).
- **Impact:** Silent data loss — container names truncated in crash alert parsing and diagnose pattern matching.
- **Fix:** Change `\w+` to `[\w.-]+` in all regex patterns.

### C5. Rate limiter TOCTOU allows duplicate alerts
- **File:** `src/main.py:322-329`
- **Issue:** `should_alert()` returns True, code `await`s Telegram send, then calls `record_alert()`. During that await, another coroutine for the same container passes `should_alert()` and sends a duplicate.
- **Impact:** Duplicate alerts sent to user during concurrent events.
- **Fix:** Call `record_alert()` immediately after `should_alert()` returns True, before the `await`.

---

## High-Priority Issues

### H1. Telegram 4096-char message limit violations
- **Files:** `ignore_command.py:196-227`, `resources_command.py:24-49`, `nl_handler.py:65`, `unraid_commands.py:145-179`, `mute_command.py:139-191`
- **Issue:** Multiple commands can exceed Telegram's 4096-char limit, causing unhandled `TelegramBadRequest`.
- **Fix:** Truncate or paginate output. Split into multiple messages when needed.

### H2. Docker stats API is extremely expensive
- **File:** `src/monitors/resource_monitor.py:179-182`
- **Issue:** `container.stats(stream=False)` takes ~1s per call (Docker collects two CPU samples). With 20 containers every 60s, that's 20 parallel 1-second Docker API calls each cycle.
- **Fix:** Consider streaming stats, only polling violating containers, or increasing default interval to 120-180s.

### H3. No Telegram send throttle — alert storms cause rate limiting
- **Files:** `src/alerts/manager.py`, `src/main.py:79-89,274,421`
- **Issue:** During server reboot (20+ container die events), alerts sent with no delay. Telegram limit is ~30 msg/sec. Server/memory alerts bypass retry logic entirely via direct `bot.send_message()`.
- **Fix:** Implement global Telegram send queue with token bucket. Batch multiple container events into single messages during storms. Route all sends through retry-capable path.

### H4. No restart loop detection
- **File:** `src/monitors/docker_events.py`
- **Issue:** Crash-looping container gets one alert, then silence for 15 minutes. No escalation.
- **Fix:** Track crash count per container. After N crashes in M minutes, send "restart loop detected" alert with count.

### H5. No bot health visibility
- **Issue:** No `/health` or `/about` command. Users can't tell if monitors are running, when they last polled, or what version is active.
- **Fix:** Add `/health` command showing: version, uptime, monitor status, last successful poll times, API status.

### H6. No startup notification
- **Issue:** Bot start/restart sends no notification. User doesn't know when bot recovered after a crash.
- **Fix:** Send brief startup message: "Bot started. Monitoring X containers, Unraid: connected/disconnected."

### H7. CPU temperature always returns `None`
- **File:** `src/unraid/client.py:211`
- **Issue:** Hardcoded to `None`. Config option `cpu_temp: 80` is dead code. Users get no alerts and no indication.
- **Fix:** Investigate Unraid GraphQL schema. If unavailable, document limitation and remove/mark config option.

### H8. AlertManagerProxy has zero tests
- **File:** `src/main.py:55-89`
- **Issue:** Central alert routing component is completely untested. Queue overflow, partial flush failures, and ordering are all blind spots.
- **Fix:** Write dedicated tests (see Test Plan section).

### H9. `pull_and_recreate` rollback path is untested
- **File:** `src/services/container_control.py:113-140`
- **Issue:** Most destructive operation in the bot. Rollback and "both fail — CRITICAL" paths have zero test coverage.
- **Fix:** Write dedicated tests (see Test Plan section).

---

## Medium-Priority Issues

### M1. Concurrent NL requests garble conversation history
- **File:** `src/services/nl_processor.py:44-98`
- **Issue:** Two messages from same user interleave during `_call_claude` await, corrupting `ConversationMemory`.
- **Fix:** Add per-user `asyncio.Lock` in `NLProcessor.process()`.

### M2. `signal.SIGALRM` regex validation blocks event loop
- **File:** `src/alerts/ignore_manager.py:54-66`
- **Issue:** `signal.alarm(1)` blocks entire event loop for up to 1s. Process-global state. Only works on main thread.
- **Fix:** Run regex validation in thread with `asyncio.to_thread` and thread-based timeout.

### M3. Alert queue can reorder messages
- **File:** `src/main.py:63-89`
- **Issue:** No lock on `_send_alert`. Flush yields at await, another coroutine can send ahead of queued alerts.
- **Fix:** Add `asyncio.Lock` to `AlertManagerProxy._send_alert`.

### M4. Config YAML `None` values crash list iteration
- **File:** `src/config.py:308-315`
- **Issue:** `ignored_containers: null` in YAML → `.get()` returns `None` not `[]` → `TypeError` on iteration.
- **Fix:** Use `self._yaml_config.get("ignored_containers") or []`.

### M5. `None` metric values crash Unraid commands
- **File:** `src/bot/unraid_commands.py:96`
- **Issue:** If `metrics["cpu_percent"]` is `None`, `f"{cpu:.1f}%"` raises `TypeError`.
- **Fix:** Add `or 0` fallbacks: `cpu = metrics.get("cpu_percent") or 0`.

### M6. Non-atomic config writes risk corruption
- **File:** `src/config.py:571-575`
- **Issue:** `open(self._path, "w")` writes directly. `os.execv` post-wizard can catch a partial write.
- **Fix:** Use `tempfile.mkstemp()` + `os.replace()` pattern (as mute/ignore managers already do).

### M7. Setup wizard treats HTTP 4xx as "connected"
- **File:** `src/bot/setup_wizard.py:225-226`
- **Issue:** `if resp.status < 500` counts 401/404 as success.
- **Fix:** Use `resp.status < 400` or check `resp.ok`.

### M8. Image pull has no timeout
- **File:** `src/services/container_control.py:91`
- **Issue:** `asyncio.to_thread(docker_client.images.pull, ...)` can hang indefinitely.
- **Fix:** Wrap in `asyncio.wait_for()` with 5-minute timeout.

### M9. Command handlers don't use Telegram retry logic
- **Files:** All command handlers
- **Issue:** `send_with_retry` exists but isn't used in interactive commands. `TelegramRetryAfter` propagates unhandled.
- **Fix:** Wrap critical `message.answer()` calls with `send_with_retry()`.

### M10. Unraid connection failure is silent
- **File:** `src/main.py:516-525`
- **Issue:** Failed Unraid connection logged but user not notified. No reconnection attempts.
- **Fix:** Notify user on failure. Add periodic reconnection with backoff.

### M11. `os.execv` restart drops pending Telegram updates
- **File:** `src/main.py:598,647`
- **Issue:** Process replaced immediately after wizard. No `dp.stop_polling()` first.
- **Fix:** Call `dp.stop_polling()` and wait before `os.execv`.

### M12. Concurrent wizard sessions can corrupt config
- **File:** `src/bot/setup_wizard.py:113-132`
- **Issue:** Two users running `/setup` simultaneously write to same `config.yaml`.
- **Fix:** Enforce single active wizard session.

### M13. Log stream queue is unbounded
- **File:** `src/monitors/log_watcher.py:149,164`
- **Issue:** `asyncio.Queue()` with no `maxsize`. Error storm grows queue without limit.
- **Fix:** Set `maxsize` (e.g., 10000 lines). Drop or block on overflow.

### M14. `RateLimiter.cleanup_stale()` is never called
- **File:** `src/alerts/rate_limiter.py:38`
- **Issue:** Method exists but nothing invokes it. Slow memory leak over months.
- **Fix:** Call periodically (e.g., start of each resource monitor poll cycle).

### M15. `load_initial_state()` blocks the event loop
- **File:** `src/monitors/docker_events.py:89-100`
- **Issue:** `containers.list(all=True)` and `parse_container()` per container are synchronous.
- **Fix:** Run via `asyncio.to_thread()`.

### M16. SSL verification disabled in wizard
- **File:** `src/bot/setup_wizard.py:219`
- **Issue:** `ssl=False` when `use_ssl=True` disables all certificate verification.
- **Fix:** Use `ssl=None` (default verification) or respect `verify_ssl` config.

### M17. Container names not escaped for Markdown
- **File:** `src/bot/commands.py:168,172-173`
- **Issue:** User input echoed in Markdown responses without escaping. Underscores/asterisks break formatting.
- **Fix:** Use `escape_markdown()` or `parse_mode=None` for error messages.

### M18. Malformed YAML raises unhandled exception in merge path
- **File:** `src/config.py:242-258`
- **Issue:** `yaml.safe_load()` not wrapped in `try/except yaml.YAMLError` in all callers.
- **Fix:** Add YAML error handling in `load_yaml_config` or all callers.

---

## Low-Priority Issues

### L1. Double signal can trigger concurrent shutdown
- **File:** `src/main.py:605,665`
- **Fix:** Add `_shutting_down` boolean guard.

### L2. `_running` flag read from thread without memory barrier
- **File:** `src/monitors/docker_events.py:206`
- **Fix:** Use `threading.Event` instead of bare boolean.

### L3. IgnoreManager read methods skip the lock
- **File:** `src/alerts/ignore_manager.py:194-236`
- **Fix:** Wrap in `with self._lock:` for consistency.

### L4. Pattern cache uses XOR of `id()` — collision risk
- **File:** `src/monitors/log_watcher.py:24`
- **Fix:** Use tuple `(id(...), id(...))` as key.

### L5. `call_soon_threadsafe` can fail during shutdown
- **File:** `src/monitors/log_watcher.py:176`
- **Fix:** Wrap `finally` block's `call_soon_threadsafe` in try/except.

### L6. Mute accepts arbitrary non-existent container names
- **File:** `src/bot/mute_command.py:90-92`
- **Fix:** Require at least one match or warn user.

### L7. `user_id` defaults to 0 for missing `from_user`
- **File:** `src/bot/ignore_command.py:67,115`
- **Fix:** Return early if `message.from_user is None`.

### L8. Config properties recreate objects on every access
- **File:** `src/config.py:350-378`
- **Fix:** Use `@functools.cached_property`.

### L9. Timezone-aware vs naive datetime mixing
- **File:** `src/models.py:14-19`
- **Fix:** Always normalize to UTC.

### L10. No exponential backoff in LogWatcher container reconnection
- **File:** `src/monitors/log_watcher.py:131-139`
- **Fix:** Implement backoff (30s → 60s → 120s → 300s cap).

### L11. ArrayMonitor internal task not tracked by `_BackgroundTasks`
- **File:** `src/unraid/monitors/array_monitor.py:47`
- **Fix:** Run loop directly in `start()` instead of spawning internal task.

### L12. Fire-and-forget `_start_monitors_safe` task not tracked
- **File:** `src/main.py:660`
- **Fix:** Store returned task and add to `bg._tasks`.

### L13. Setup wizard toggle callback swallows all exceptions
- **File:** `src/bot/setup_wizard.py:692-696`
- **Fix:** Log at DEBUG level.

### L14. `_tool_start_container` executes without confirmation
- **File:** `src/services/nl_tools.py:516-530`
- **Note:** By design (safe operation), but inconsistent with restart/stop/pull.

### L15. `/logs` error swallowed without logging
- **File:** `src/bot/commands.py:244-245`
- **Fix:** Add `logger.error(f"Error getting logs: {e}", exc_info=True)`.

### L16. Setup wizard host input not validated
- **File:** `src/bot/setup_wizard.py:577-608`
- **Fix:** Regex check for IP/hostname format, length limit.

### L17. NL confirm callback container name not validated
- **File:** `src/bot/nl_handler.py:80-127`
- **Fix:** Add container name validation consistent with `alert_callbacks.py`.

---

## Feature Gaps

### F1. Restart loop detection (High)
Track crash count per container. Escalate with "crashed N times in M minutes" alert.

### F2. Maintenance/quiet mode (High)
`/quiet 2h` to suppress ALL alerts during reboots or planned work.

### F3. Bot health command (High)
`/health` showing uptime, version, monitor status, last poll times.

### F4. Startup notification (Medium)
Send "Bot started. Monitoring X containers, Unraid: connected/disconnected" on boot.

### F5. Mute/unmute via NL (Medium)
"mute plex for 2 hours" should work through natural language tools.

### F6. Test alert command (Medium)
`/test-alert` sends a sample crash alert with quick-action buttons.

### F7. Dynamic watch/unwatch (Medium)
`/watch <container>` and `/unwatch` without full `/setup` restart.

### F8. Parity check monitoring (Medium)
Start/progress/completion notifications for Unraid parity checks.

### F9. Alert batching during storms (Medium)
"5 containers crashed: plex, radarr, sonarr..." as a single message.

### F10. Button-based confirmations (Medium)
Replace text "yes" with inline keyboard buttons for destructive actions.

### F11. NL capability discovery (Medium)
`/help` should prominently mention "just ask me in plain English."

### F12. Per-container log cooldown (Medium)
Different alert cooldowns for noisy vs critical containers.

### F13. Unraid reconnection (Medium)
Auto-retry with backoff when Unraid API goes down. Notify user.

### F14. SMART data monitoring (Medium)
Alert on pre-failure disk indicators (reallocated sectors, pending sectors).

### F15. Diagnose via NL (Low)
"diagnose plex" through NL should use the optimized DiagnosticService.

### F16. Back to dashboard button (Low)
`/manage` sub-views should have a return button.

---

## Test Plan

### Tier 1: Catch data loss and silent failures

**AlertManagerProxy queue-then-flush:**
- Alerts queued before chat_id are delivered in order when set
- Queue overflow at MAX_QUEUED=50 drops gracefully
- Partial failure during flush still delivers remaining alerts

**`pull_and_recreate` rollback paths:**
- Recreate fails, rollback to old image succeeds — user gets rollback message
- Both recreate AND rollback fail — user gets CRITICAL message
- Rollback with `old_image_id=None` — doesn't crash

**Rate limiter TOCTOU prevention:**
- Two crash events same container same tick — only one alert sent
- Verify `record_alert()` called before `await` send

**`on_log_error` callback end-to-end:**
- Muted container — no alert
- Rate limiter allows — alert with correct suppressed count
- Rate limiter blocks — count incremented, no alert

### Tier 2: Catch common production scenarios

**Crash loop rate limiting:**
- 10 rapid crash events same container — 1 alert, suppressed=9
- After cooldown — next event triggers new alert with accumulated count

**Server reboot (exit code 143):**
- 20 containers die with SIGTERM simultaneously
- Document/validate current behavior (currently triggers crash alerts)

**Mute + crash alert integration:**
- Muted container crash event — `send_crash_alert` NOT called
- Unmute — same event — alert IS sent

**LogWatcher container reconnection:**
- `_stream_logs` raises `NotFound` — waits 30s, retries
- Container comes back — reconnects and resumes

**DockerEventMonitor reconnection with backoff:**
- `_event_loop` raises — exponential backoff 1s→2s→...→60s cap
- Success resets backoff
- `stop()` during backoff breaks loop

### Tier 3: Edge cases and config problems

**Telegram message length safety:**
- `/ignores` with many containers — truncated/paginated, no crash
- `/resources` with 80 containers — same
- NL response at max tokens — truncated to 4096

**Hyphenated container names:**
- `extract_container_from_alert("*CRASHED:* my-app")` → `"my-app"` not `"my"`
- Same for `CRASH_ALERT_PATTERN`

**Config YAML edge cases:**
- `ignored_containers: null` — treated as empty list
- `cooldown_seconds: "not_a_number"` — clear error or safe default
- Missing `log_watching` section — defaults applied

**Shutdown resilience:**
- `monitor.stop()` raises — remaining cleanup still runs
- All tasks cancelled and awaited within timeout

**Unraid API temporary failure:**
- `UnraidConnectionError` on first poll — loop continues
- Second poll succeeds — metrics reported

**`parse_container` with deleted image:**
- `image.tags` raises `ImageNotFound` — falls back to `config.Image`

---

## Implementation Order

1. **Critical bugs C1-C5** — immediate fixes
2. **High-priority H1, H3** — Telegram message safety and send throttle
3. **Tier 1 tests** — cover the critical paths
4. **Medium-priority M1-M6** — reliability improvements
5. **High-priority H4-H6** — restart loop detection, health command, startup notification
6. **Tier 2 tests** — production scenario coverage
7. **Remaining medium-priority** — M7-M18
8. **Feature gaps** — F1-F16 based on user priorities
9. **Low-priority + Tier 3 tests** — hygiene and edge cases
