# Codebase Audit Findings — UnraidMonitor

## Date: 2026-03-03
## Scope: Full codebase (55 source files, 73 test files, build/config files)

---

## Summary: 0 Critical, 3 High, 20 Medium, 31 Low

---

## High Priority

### F1 — Callback data exceeding Telegram's 64-byte limit
Telegram enforces a 64-byte limit on `callback_data`. No validation exists anywhere. Docker Compose container names can be 50+ chars, and prefixes like `ctrl_confirm:restart:` add 22 bytes.
- Affected: `src/alerts/manager.py`, `src/bot/control_commands.py`, `src/bot/diagnose_command.py`, `src/bot/manage_command.py`, `src/bot/setup_wizard.py`, `src/bot/alert_callbacks.py`, `src/bot/nl_handler.py`

### F9 — Stale container data after image pull
`src/services/container_control.py:117` — After pulling a new image and restarting, `container.attrs` returns stale data. Need `container.reload()` before reading attrs.

### F43 — Security-critical utility functions untested
7 functions in `src/utils/formatting.py` have zero test coverage:
- `validate_container_name`, `safe_reply`, `safe_edit`, `escape_markdown`
- `truncate_message`, `format_mute_expiry`, `extract_container_from_alert`

---

## Medium Priority

### F2 — Container name regex allows path traversal characters
`src/utils/formatting.py:11` — `_VALID_CONTAINER_NAME` regex allows `/`.

### F3 — Setup wizard disables SSL verification
`src/bot/setup_wizard.py` — No warning or opt-in for disabled SSL.

### F4 — Hex token redaction too aggressive
`src/utils/sanitize.py:91` — Matches Docker container IDs, Git SHAs, UUIDs.

### F5 — Overly permissive umask in entrypoint.sh
`entrypoint.sh:26` — `umask 0000` makes all files world-readable/writable.

### F6 — Unpinned Docker base image
`Dockerfile:5` — `FROM python:3.11-slim` without SHA digest pin.

### F7 — Dependency inconsistencies
- `openai` in `requirements.txt` but not `pyproject.toml`
- `anthropic` required in `pyproject.toml` but optional in code
- `docker-compose.yml` missing `OPENAI_API_KEY` and `OLLAMA_HOST`

### F10 — Alerts sent to single user in some paths
`src/main.py:303,450,498,623,635` — Use `get_chat_id()` instead of `get_all_chat_ids()`.

### F11 — Inconsistent parse_mode across alert paths
- `on_server_alert`: no parse_mode
- `on_memory_alert`: parse_mode="Markdown" with no escaping
- `nl_handler`: no parse_mode on LLM responses

### F12 — Config threshold ordering not validated
`src/config.py:203-212` — `MemoryConfig` doesn't validate `critical > warning > safe`.

### F13 — manage_command uses answer() instead of safe_edit()
Causes message spam on navigation.

### F14 — NL processor tool-loop message format
`src/services/nl_processor.py:287-296` — Normalized dict doesn't match provider expectations.

### F15 — Unraid client sets _connected before verification
`src/unraid/client.py:130` — `_connected = True` before test query.

### F16 — Shared cache timestamp in UnraidSystemMonitor
`src/unraid/monitors/system_monitor.py:166-173` — Single `_cache_time` for metrics and array.

### F26 — UPS mute methods are dead code
`src/alerts/server_mute_manager.py` — `is_ups_muted()`, `mute_ups()`, `unmute_ups()` with no UPS monitoring.

### F29 — Unbounded caches
- `src/analysis/pattern_analyzer.py:54` — `_cache` grows without eviction
- `src/services/nl_processor.py:170` — `_user_locks` grows without bound

### F36 — Container names with underscores not escaped in Markdown
Affects 10+ files. Underscores are common in Docker container names.

### F39 — IgnoreManager read methods lack lock protection
`src/alerts/ignore_manager.py:227,235,249` — Could raise RuntimeError.

### F40 — IgnoreManager batch_updates() sets flag without lock
`src/alerts/ignore_manager.py:127,131`

### F41 — RateLimiter has no thread safety
`src/alerts/rate_limiter.py` — No lock on shared dicts.

### F44 — PerUserRateLimiter almost untested
Only 1 test checking internal deque type.

### F45 — sanitize_logs_for_display untested
All 7 redaction patterns have zero test coverage.

### F47 — Unraid polling intervals not clamped
`src/config.py:243-244` — `poll_system_seconds: 0` causes tight loop.

---

## Low Priority

### F8 — Version mismatch
`pyproject.toml` says `0.8.3`, CLAUDE.md/README say `v0.9.0`.

### F17 — _strip_markdown missing bracket characters
`src/utils/formatting.py:22-23` — Missing `[`/`]` stripping.

### F18 — Inconsistent unit formatting in Unraid commands
Binary units vs decimal units in different functions.

### F19 — rate_limiter get_retry_after misses hourly limit
Returns 0 even when hourly limit is exceeded.

### F20 — Hardcoded "15 minutes" in alert text
`src/alerts/manager.py:114` — Doesn't respect configurable cooldown.

### F21 — Log truncation adds prefix after slicing
`src/bot/commands.py` — Can exceed Telegram's 4096 char limit.

### F22 — No upper bound on retry_after sleep
`src/utils/telegram_retry.py:37-43`

### F23 — os.execv skips cleanup
`src/main.py:733,784`

### F24 — DEFAULT_LOG_WATCHING dict mutation
`src/config.py:351-355`

### F25 — Empty ignore_similar button with very long names
`src/alerts/manager.py:131`

### F27 — Author-specific default container
`src/config.py:26` — "Brisbooks" in defaults.

### F28 — Duration parser missing "d" support
`src/alerts/mute_manager.py:11`

### F30 — Redundant strip_log_timestamps call
`src/alerts/ignore_manager.py:143,151`

### F31 — ProviderRegistry creates new provider per call
`src/services/llm/registry.py`

### F35 — Expired mute entries not marked dirty
`src/alerts/base_mute_manager.py:44-46`

### F37 — Direct access to private attributes
`src/main.py:569`, `health_command.py`, `ignore_command.py`, `resources_command.py`

### F38 — Duplicated alert pattern matching
`src/bot/diagnose_command.py` and `src/utils/formatting.py`

### F42 — Naive datetime mixed usage
All alert/mute files vs timezone-aware in models.py.

### F46 — Weak test assertions in test_sanitize.py
`tests/test_sanitize.py:38,44` — `or` patterns trivially satisfiable.

### F48 — docker-compose.yml missing multi-provider env vars
`OPENAI_API_KEY` and `OLLAMA_HOST` not listed.

### F49 — Signal handler task not stored
`src/main.py` — Could theoretically be garbage collected.

### F50 — No pre-commit hooks
No `.pre-commit-config.yaml`.

### F51 — No CI/CD pipeline
No `.github/workflows/`.

### F52 — In-memory state lost on restart
Appropriate for single-instance but worth noting.

### F53 — No timeout on Ollama model discovery
Relies on global aiohttp session timeout.

### F54 — No LICENSE file
