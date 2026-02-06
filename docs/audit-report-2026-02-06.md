# Comprehensive Codebase Audit Report

**Project:** Unraid Server Monitor Bot v0.7.2
**Date:** 2026-02-06
**Auditors:** 6 specialized AI agents (Security, Test Coverage, Bug Hunter, Performance, Dead Code, Schema Consistency)
**Scope:** Full codebase -- 48 source files, 60 test files

---

## Executive Summary

Six specialized agents performed an independent, parallel audit of the entire codebase. After deduplication and cross-referencing, **87 unique findings** were identified across security, correctness, performance, testing, schema consistency, and code hygiene.

**Finding Distribution:**

| Priority | Count | Description |
|----------|-------|-------------|
| **P0 - Critical** | 8 | Must fix -- security holes, data loss risks, runtime crashes |
| **P1 - Important** | 19 | Should fix soon -- event loop blocking, missing auth, logic errors |
| **P2 - Moderate** | 28 | Plan to fix -- memory leaks, test gaps, config inconsistencies |
| **P3 - Low** | 32 | Nice to have -- dead code cleanup, minor edge cases |

---

## P0 - Critical (Fix Immediately)

### P0-1: `pull_and_recreate()` Has No Rollback and Incomplete Config Extraction
**Sources:** Bug Hunter (BUG 34, 35), Test Coverage
**Files:** `src/services/container_control.py:69-102`

`pull_and_recreate()` stops a container, removes it, then recreates it using `_extract_run_config()`. Two critical problems:
1. **No rollback:** If recreation fails (port conflicts, missing volumes, image pull error), the original container is permanently deleted with no recovery path.
2. **Incomplete config:** `_extract_run_config()` only extracts 5 properties (Env, Binds, PortBindings, RestartPolicy, NetworkMode). It misses capabilities, devices, labels, health checks, memory/CPU limits, privileged mode, user, entrypoint, command, DNS, logging config, ulimits, sysctls, etc.
3. **Zero test coverage:** `pull_and_recreate()` and `_extract_run_config()` have no tests.

**Impact:** Using `/pull` to update a container can permanently destroy it.

### P0-2: NL Confirm Callback Bypasses Protected Container Check
**Sources:** Security Auditor (Finding 1), Bug Hunter (BUG 15)
**File:** `src/bot/nl_handler.py:80-117`

The `create_nl_confirm_callback` handler executes container actions (restart/stop/start/pull) without checking the protected container list. The container name comes from callback data (`nl_confirm:action:container`) which could be modified by a Telegram user.

**Impact:** Authenticated users can bypass the protected container list to restart/stop protected containers.

### P0-3: Restart/Alert Callback Bypasses Protected Container Check
**Sources:** Security Auditor (Finding 2)
**File:** `src/bot/alert_callbacks.py:32-75`

The `restart_callback` handler executes `controller.restart(actual_name)` without checking `controller.is_protected()`. The regular `/restart` command checks this, but the inline button path does not.

**Impact:** Same as P0-2 -- protected containers can be restarted via alert buttons.

### P0-4: Auth Middleware Not Applied to Callback Queries
**Sources:** Bug Hunter (BUG 23)
**File:** `src/bot/telegram_bot.py:187`

`AuthMiddleware` is only registered on `dp.message`, not on `dp.callback_query`. All inline button handlers (restart, logs, diagnose, mute, ignore, manage, nl_confirm) are unprotected by authentication.

**Impact:** Any Telegram user who obtains a valid callback_data string (e.g., from a forwarded alert message) could invoke actions like restarting containers.

### P0-5: `manage_command.py` Calls Non-Existent `array_mute_manager.unmute()`
**Sources:** Schema Consistency (Finding 14)
**File:** `src/bot/manage_command.py:397`

Code calls `array_mute_manager.unmute()` but `ArrayMuteManager` has no `unmute()` method. The correct method is `unmute_array()`.

**Impact:** Runtime `AttributeError` crash when removing an array mute through the `/manage` dashboard.

### P0-6: NL Tool `get_array_status` Parses Wrong Schema
**Sources:** Schema Consistency (Finding 9)
**Files:** `src/services/nl_tools.py:424-469`, `src/unraid/client.py:252-259`

The NL tool handler expects `status == "healthy"`, `used_bytes`/`total_bytes` fields, and `parity_status` keys. The actual GraphQL response uses `"DISK_OK"`, `capacity.kilobytes.used/total`, and has no parity fields. Every disk would be reported as "unhealthy" and capacity would always be 0.

**Impact:** The NL `get_array_status` tool returns completely incorrect information.

### P0-7: `cpu_temperature` Always Returns 0 -- Monitoring Broken
**Sources:** Schema Consistency (Finding 10)
**Files:** `src/unraid/client.py:245`, `src/unraid/monitors/system_monitor.py:88-95`

`cpu_temperature` is hardcoded to `0` in `get_system_metrics()`. The CPU temp threshold check (default 80C) is dead code. The `/server` command displays `0.0C` misleadingly.

**Impact:** CPU temperature monitoring is completely non-functional. Users get false sense of security.

### P0-8: Server Mute Removal via Manage Dashboard Only Partially Unmutes
**Sources:** Schema Consistency (Finding 15)
**File:** `src/bot/manage_command.py:392`

Code calls `server_mute_manager.remove_mute("server")` which only removes the "server" category, but `mute_server()` sets three categories: "server", "array", "ups". The correct method is `unmute_server()`.

**Impact:** User thinks they unmuted server alerts, but array and UPS alerts remain muted.

---

## P1 - Important (Fix Within 1-2 Weeks)

### P1-1: Synchronous Anthropic API Calls Block Event Loop
**Sources:** Performance (1.1, 1.2, 1.3), Bug Hunter (BUG 3, 4, 5), Security (Finding 7), Schema (Finding 17)
**Files:** `src/services/nl_processor.py:238-244,278-284`, `src/services/diagnostic.py:158,248`, `src/analysis/pattern_analyzer.py:82`

All three Claude API callers use the synchronous `anthropic.Anthropic` client inside async methods. Each call blocks the entire event loop for 2-30 seconds. The NL processor's tool loop can block for minutes.

**Fix:** Switch to `anthropic.AsyncAnthropic` or wrap calls in `asyncio.to_thread()`.

### P1-2: Synchronous Docker API Calls Block Event Loop
**Sources:** Performance (1.4-1.7), Bug Hunter (BUG 1, 2, 14)
**Files:** `src/monitors/memory_monitor.py:90,230`, `src/services/diagnostic.py:89-112`, `src/bot/commands.py:218-220`, `src/services/nl_tools.py:333-334`

Multiple locations call Docker SDK methods synchronously in async context: `container.stop()`, `container.start()`, `container.logs()`, `containers.get()`.

**Fix:** Wrap all blocking Docker calls in `asyncio.to_thread()`.

### P1-3: Sequential Docker Stats Creates N+1 Query Pattern
**Sources:** Performance (2.1, 2.2)
**File:** `src/monitors/resource_monitor.py:169-193`

`get_all_stats()` calls `container.stats(stream=False)` sequentially for each container. Each call takes 1-2 seconds. With 20+ containers, poll cycles take 20-40 seconds.

**Fix:** Parallelize with `asyncio.gather()`.

### P1-4: `asyncio.Queue` Used From Non-Event-Loop Threads
**Sources:** Performance (9.1), Bug Hunter (BUG 11, 12)
**Files:** `src/monitors/log_watcher.py:148`, `src/monitors/docker_events.py:210-218`

`asyncio.Queue.put_nowait()` is called from threads running via `asyncio.to_thread()`. `asyncio.Queue` is not thread-safe.

**Fix:** Use `loop.call_soon_threadsafe(queue.put_nowait, item)` or use `janus` queue.

### P1-5: Unraid API Communication Over Unencrypted HTTP
**Sources:** Security (Finding 3)
**Files:** `config/config.yaml`, `src/config.py:467-480`

Default config uses `use_ssl: false` on port 80. The API key is transmitted in cleartext headers.

**Fix:** Change default template to `use_ssl: true`, `port: 443`.

### P1-6: Error Messages Leak Exception Details to Telegram
**Sources:** Security (Finding 6)
**File:** `src/bot/alert_callbacks.py:160`

Raw exception messages from Docker SDK are sent directly to Telegram users: `f"Error getting logs: {e}"`.

**Fix:** Use generic error messages; log details server-side.

### P1-7: Silent Alert Loss When No Chat ID
**Sources:** Bug Hunter (BUG 7)
**File:** `src/main.py:43-50`

When no user has sent `/start`, all alerts are silently dropped. Container crashes during startup window are permanently lost.

**Fix:** Queue alerts and deliver when chat ID becomes available.

### P1-8: `from_user` Could Be None -- Potential AttributeError
**Sources:** Bug Hunter (BUG 8)
**Files:** `src/bot/control_commands.py:75,128`, `src/bot/telegram_bot.py:127`, `src/bot/diagnose_command.py:40`

`message.from_user.id` accessed without None check. In channel posts or anonymous admin messages, `from_user` is None.

**Fix:** Add None guard at each location.

### P1-9: `callback.message` Could Be None in Ignore Callback
**Sources:** Bug Hunter (BUG 9)
**File:** `src/bot/ignore_command.py:260,273`

`callback.message.answer(...)` called without checking if `callback.message` is None. Other handlers check correctly.

**Fix:** Add None check consistent with other handlers.

### P1-10: Unraid System Monitor Sends Repeated Alerts Without Rate Limiting
**Sources:** Performance (4.1, 4.2)
**Files:** `src/unraid/monitors/system_monitor.py:86-116`, `src/main.py:150-156`

Every 30-second poll sends a duplicate alert if a threshold is exceeded. Also bypasses `send_with_retry()` retry logic.

**Fix:** Add rate limiting (similar to `RateLimiter` used by Docker monitors). Use `send_with_retry()`.

### P1-11: Log Watcher Thread Cannot Be Cancelled / Leaks on Restart
**Sources:** Bug Hunter (BUG 10)
**File:** `src/monitors/log_watcher.py:155-156`

The blocking `container.logs(stream=True, follow=True)` iterator in the thread doesn't get interrupted on cancellation. Orphaned threads accumulate on container restarts.

**Fix:** Use the Docker SDK's `close()` method on the log generator or set a timeout.

### P1-12: No Locking in BaseMuteManager and IgnoreManager
**Sources:** Bug Hunter (BUG 16, 17)
**Files:** `src/alerts/base_mute_manager.py`, `src/alerts/ignore_manager.py`

Concurrent reads/writes from monitors and Telegram handlers can race, causing lost mutes or `RuntimeError: dictionary changed size during iteration`.

**Fix:** Add `asyncio.Lock` to mutable operations.

### P1-13: Unraid aiohttp Session Has No Timeout
**Sources:** Performance (6.2)
**File:** `src/unraid/client.py:132-161`

Default aiohttp timeout is 300 seconds. An unresponsive Unraid server stalls the monitor loop for 5 minutes per query.

**Fix:** Set `timeout=aiohttp.ClientTimeout(total=10)`.

### P1-14: AI-Generated Regex Patterns May Cause ReDoS
**Sources:** Security (Finding 5)
**Files:** `src/analysis/pattern_analyzer.py:96-111`, `src/alerts/ignore_manager.py:17-23`

The ReDoS check uses a limited blocklist of 5 known-bad patterns. Novel catastrophic backtracking patterns can bypass it.

**Fix:** Add timeout-based regex validation or restrict to substring patterns only.

### P1-15: Real IP Address Hardcoded in Default Config Template
**Sources:** Security (Finding 8)
**File:** `src/config.py:469`

The source-committed template contains `host: "192.168.0.190"`.

**Fix:** Replace with `"your-unraid-ip"` placeholder.

### P1-16: `pyproject.toml` Missing Most Runtime Dependencies
**Sources:** Schema Consistency (Finding 27)
**File:** `pyproject.toml`

Only declares `docker` and `unraid-api`. Missing: aiogram, pyyaml, pydantic, pydantic-settings, anthropic, psutil. `pip install .` produces a broken installation.

**Fix:** Add all runtime dependencies to `pyproject.toml`.

### P1-17: `aiohttp` Used But Not Declared in Any Dependency File
**Sources:** Schema Consistency (Finding 28)
**File:** `src/unraid/client.py:13`, `requirements.txt`

`aiohttp` is imported directly but only available as a transitive dependency of aiogram.

**Fix:** Add `aiohttp` to `requirements.txt` and `pyproject.toml`.

### P1-18: `containers.list(all=True)` Fetches Excessive Data
**Sources:** Performance (5.1)
**File:** `src/monitors/resource_monitor.py:177`

Fetches full inspect data for all containers (including stopped) every 60 seconds. Only needs running containers' names.

**Fix:** Use `containers.list(filters={"status": "running"})`.

### P1-19: Docker Socket Provides Root-Equivalent Access
**Sources:** Security (Finding 4)
**File:** `docker-compose.yml:23`

The `:ro` mount flag on Docker socket only prevents deletion of the socket file, not API access. A compromised bot has full Docker control.

**Fix:** Consider `tecnativa/docker-socket-proxy` to restrict exposed API endpoints. Document the risk.

---

## P2 - Moderate (Fix Within 1-2 Months)

### P2-1: Zero Test Coverage for `pull_and_recreate()` and `_extract_run_config()`
**Source:** Test Coverage
**File:** `src/services/container_control.py`

### P2-2: Zero Test Coverage for `DockerEventMonitor` Reconnection Loop
**Source:** Test Coverage
**File:** `src/monitors/docker_events.py` -- `start()`, `_event_loop()`, `_reconnect()`, `connect()`, `load_initial_state()`, `stop()`

### P2-3: Zero Test Coverage for `AlertManagerProxy`
**Source:** Test Coverage
**File:** `src/main.py:35-59` -- all 3 delegate methods when chat_id is None/set

### P2-4: Control Command Handlers Only Test `restart` -- Not `stop`, `start`, `pull`
**Source:** Test Coverage
**File:** `src/bot/control_commands.py`

### P2-5: NL Processor Rate Limiting, Message Length Validation, Max Tool Iterations Untested
**Source:** Test Coverage
**File:** `src/services/nl_processor.py`

### P2-6: `MemoryStore` TTL Expiration and Max-Users Eviction Untested
**Source:** Test Coverage
**File:** `src/services/nl_processor.py`

### P2-7: `LogWatcher._watch_container()` Retry Loop Untested
**Source:** Test Coverage
**File:** `src/monitors/log_watcher.py`

### P2-8: `ConfirmationManager.cancel()` Untested
**Source:** Test Coverage
**File:** `src/bot/confirmation.py`

### P2-9: `AlertManager` Error Line Truncation, Exit Code Interpretation Untested
**Source:** Test Coverage
**File:** `src/alerts/manager.py`

### P2-10: RateLimiter Never Cleans Up Stale Entries
**Sources:** Performance (3.1), Bug Hunter (BUG 32)
**File:** `src/alerts/rate_limiter.py`

Dictionaries grow indefinitely with every unique container name.

### P2-11: DiagnosticService `_pending` Dict Never Cleaned Proactively
**Sources:** Performance (3.3), Bug Hunter (BUG 20)
**File:** `src/services/diagnostic.py:76`

### P2-12: ConfirmationManager Never Cleaned Up
**Sources:** Bug Hunter (BUG 21)
**File:** `src/bot/confirmation.py`

### P2-13: IgnoreSelectionState and ManageSelectionState Never Cleaned Up
**Sources:** Bug Hunter (BUG 22)
**Files:** `src/bot/ignore_command.py:28`, `src/bot/manage_command.py:24`

### P2-14: Mixed Threading/Asyncio Primitives in Memory Monitor
**Sources:** Performance (9.2), Bug Hunter (BUG 6)
**File:** `src/monitors/memory_monitor.py:60`

### P2-15: Double Task Creation in Unraid System Monitor
**Sources:** Bug Hunter (BUG 13)
**File:** `src/unraid/monitors/system_monitor.py:46`, `src/main.py:345`

### P2-16: Ghost Containers After Reconnect
**Sources:** Bug Hunter (BUG 26)
**File:** `src/monitors/docker_events.py:154`

### P2-17: Callback Data for `ignore_similar` May Exceed Telegram's 64-Byte Limit
**Sources:** Schema Consistency (Finding 8)
**File:** `src/alerts/manager.py:130-132`

### P2-18: Container Names With Colons Break Multiple Callback Parsers
**Sources:** Bug Hunter (BUG 19, 31), Security (Finding 11), Schema Consistency (Finding 24)
**Files:** `src/bot/manage_command.py:231`, `src/services/nl_processor.py:262`

### P2-19: Unpinned Dependency Versions
**Sources:** Security (Finding 10)
**File:** `requirements.txt`

### P2-20: Dockerfile Does Not Pin Base Image Digest
**Sources:** Security (Finding 13)
**File:** `Dockerfile:1`

### P2-21: Config Template Missing `resource_monitoring` Section
**Sources:** Schema Consistency (Finding 1)
**File:** `src/config.py`

### P2-22: Config Template Missing `panic` and `traceback` Error Patterns
**Sources:** Schema Consistency (Finding 4)
**File:** `src/config.py`

### P2-23: Default Config Template vs Code Defaults Divergence
**Sources:** Schema Consistency (Findings 3, 5, 6)
**File:** `src/config.py`

### P2-24: `format_server_detailed` Reads Non-Existent Metric Keys
**Sources:** Schema Consistency (Finding 11), Dead Code (4.4)
**File:** `src/bot/unraid_commands.py:97-98`

### P2-25: `python-dotenv` Not Declared as Dependency
**Sources:** Schema Consistency (Finding 29)
**File:** `requirements.txt`

### P2-26: Markdown Injection in User-Facing Messages
**Sources:** Bug Hunter (BUG 30)
**File:** `src/bot/commands.py:230` and others

Container names with `*`, `_`, `` ` `` characters break Telegram Markdown formatting.

### P2-27: Telegram Message Length Not Validated With Header
**Sources:** Performance (10.2)
**File:** `src/bot/commands.py:230-231`

Log truncation doesn't account for header/footer length, potentially exceeding Telegram's 4096 char limit.

### P2-28: `TELEGRAM_ALLOWED_USERS` Not Documented in CLAUDE.md
**Sources:** Schema Consistency (Finding 18)

---

## P3 - Low (Backlog / Nice to Have)

### Dead Code -- Safe to Remove Immediately
| ID | Item | File |
|----|------|------|
| P3-1 | `asdict` unused import | `src/alerts/ignore_manager.py:6` |
| P3-2 | `Bot` unused import | `src/bot/alert_callbacks.py:8` |
| P3-3 | `is_action_tool()`, `is_read_only_tool()`, `READ_ONLY_TOOLS`, `ACTION_TOOLS` | `src/services/nl_tools.py:177-216` |
| P3-4 | `ConfirmationManager.cancel()` | `src/bot/confirmation.py:51-56` |
| P3-5 | `AppConfig.settings` property | `src/config.py:311-314` |
| P3-6 | `last_error` variable (assigned, never read) | `src/utils/telegram_retry.py:33,46,72,86` |
| P3-7 | `new_container` variable (assigned, never read) | `src/services/container_control.py:87` |
| P3-8 | `hostname` key in metrics dict | `src/unraid/client.py:243` |
| P3-9 | Unused `__init__.py` re-exports | `src/unraid/__init__.py`, `src/unraid/monitors/__init__.py` |

### Code Duplication
| ID | Item | Files |
|----|------|-------|
| P3-10 | 3x `format_uptime` functions | `utils/formatting.py`, `alerts/manager.py:27`, `services/diagnostic.py:169` |
| P3-11 | 2x `extract_container_from_alert()` | `bot/ignore_command.py:18`, `bot/mute_command.py:27` |

### Dead Configuration / Incomplete Features
| ID | Item | File |
|----|------|------|
| P3-12 | `poll_ups_seconds`, `ups_battery_threshold` (no UPS monitor) | `src/config.py:215,221` |
| P3-13 | `VMS_QUERY`, `UPS_QUERY` (no callers) | `src/unraid/client.py:50-81` |
| P3-14 | `get_vms()`, `get_ups_status()` (no callers) | `src/unraid/client.py:261-278` |
| P3-15 | Incomplete memory kill restart confirmation UI | `src/main.py:258` (TODO) |
| P3-16 | `docker_gid` setting (intentionally unused) | `src/config.py:276` |
| P3-17 | Redundant `TelegramRetryAfter` catches in AlertManager | `src/alerts/manager.py:94-99,156-161,232-237` |

### Minor Edge Cases
| ID | Item | File |
|----|------|------|
| P3-18 | Negative memory after cache subtraction | `src/monitors/resource_monitor.py:95` |
| P3-19 | `parse_duration` accepts "0m"/"0h" | `src/alerts/mute_manager.py:33` |
| P3-20 | `uptime_seconds` could be negative with clock skew | `src/models.py:18` |
| P3-21 | Exception swallowing during shutdown | `src/monitors/docker_events.py:225-228` |
| P3-22 | TOCTOU race in cancel_kill_command | `src/bot/memory_commands.py:24-26` |
| P3-23 | `container_name` type safety in control_commands | `src/bot/control_commands.py:68` |
| P3-24 | PerUserRateLimiter retains empty entries | `src/utils/rate_limiter.py` |
| P3-25 | ResourceMonitor violations dict retains stale entries | `src/monitors/resource_monitor.py:161` |
| P3-26 | AlertManagerProxy creates new AlertManager per alert | `src/main.py:43-48` |
| P3-27 | Tool definitions rebuilt on every API call | `src/services/nl_processor.py:234` |
| P3-28 | Error/ignore patterns lowercased on every log line | `src/monitors/log_watcher.py:14-32`, `src/alerts/ignore_manager.py:96-109` |
| P3-29 | Double log truncation in NL tools | `src/services/nl_tools.py:338-343` |
| P3-30 | Mute expiry check triggers inline file I/O | `src/alerts/base_mute_manager.py:29-41` |
| P3-31 | No Telegram message batching for burst alerts | `src/alerts/manager.py` |
| P3-32 | Multiple independent Docker client instances | `src/main.py:188-189,213-221` |

---

## Positive Observations

The audit identified several strong security and engineering patterns:

1. **Authentication middleware** with Telegram user ID allowlist and silent dropping
2. **Protected containers list** preventing accidental damage to critical infrastructure
3. **Confirmation prompts** with timeout for destructive operations
4. **Prompt injection sanitization** (`sanitize.py`) filtering known injection patterns
5. **Log redaction** (`sanitize_logs_for_display`) stripping secrets before Telegram display
6. **ReDoS validation** on user-submitted regex patterns with length limits
7. **Rate limiting** on NL processing (per-user per-minute/hour) and alert sending
8. **Non-root container execution** in Dockerfile
9. **Message length validation** (2000 char limit on NL input)
10. **Container name validation** with regex whitelist in callback handlers
11. **Secrets in environment variables** (not hardcoded), `.env` gitignored
12. **Atomic JSON writes** preventing persistence corruption
13. **`TYPE_CHECKING` guards** preventing circular imports

---

## Recommended Fix Order

### Sprint 1 (Week 1): Critical Security & Crashes
- P0-2, P0-3: Add `is_protected()` checks to callback handlers
- P0-4: Register `AuthMiddleware` on `dp.callback_query`
- P0-5: Fix `unmute()` -> `unmute_array()` call
- P0-8: Fix `remove_mute("server")` -> `unmute_server()` call
- P1-6: Replace raw exception messages with generic errors
- P1-8: Add `from_user` None guards
- P1-9: Add `callback.message` None check

### Sprint 2 (Week 2): Event Loop & Data Integrity
- P1-1: Switch to `AsyncAnthropic` client (fixes 3 files)
- P1-2: Wrap Docker calls in `asyncio.to_thread()` (fixes 4 files)
- P1-3: Parallelize `get_all_stats()` with `asyncio.gather()`
- P1-4: Fix thread-unsafe `asyncio.Queue` usage
- P1-12: Add `asyncio.Lock` to mute/ignore managers
- P1-13: Set aiohttp session timeout

### Sprint 3 (Week 3-4): Broken Features & Schema Fixes
- P0-1: Redesign `pull_and_recreate()` with rollback and full config extraction
- P0-6: Fix NL tool `get_array_status` schema parsing
- P0-7: Implement CPU temperature GraphQL query or remove dead threshold
- P1-10: Add rate limiting to Unraid system monitor alerts
- P1-15: Replace hardcoded IP with placeholder
- P1-16, P1-17: Fix dependency declarations

### Sprint 4 (Month 2): Test Coverage & Cleanup
- P2-1 through P2-9: Fill critical test coverage gaps
- P3-1 through P3-9: Remove dead code
- P3-10, P3-11: Deduplicate utility functions
- Remaining P2 items as capacity allows
