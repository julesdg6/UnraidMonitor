# Changelog

All notable changes to UnraidMonitor will be documented in this file.

## [0.7.4] - 2026-02-10

### Changed
- **PUID/PGID entrypoint for Unraid permissions** - Container now starts as root and uses an entrypoint script to fix ownership of bind-mounted `/app/config` and `/app/data` directories to `PUID:PGID` (defaults to `99:100` = `nobody:users`), then drops privileges via `gosu`. Fixes root-owned appdata folders created by Community Apps on first install.
- **Permissive file creation** - Set `umask 0000` in entrypoint and added `os.fchmod(fd, 0o666)` to mute/ignore JSON writers so all created files (config.yaml, mute/ignore JSON) are `rw-rw-rw-` instead of owner-only

### Added
- `entrypoint.sh` - Privilege-drop entrypoint that sets directory ownership and runs as non-root user
- `PUID` and `PGID` environment variables (default `99`/`100`) for configurable file ownership
- `gosu` package in Docker image for secure privilege dropping

## [0.7.3] - 2026-02-06

### Security
- **Auth middleware on callback queries** - Authentication was only applied to message handlers, not inline button callbacks. Any user with a forwarded alert could invoke actions. Now enforced on all callback queries (P0-4)
- **Protected container bypass via callbacks** - NL confirm callback and alert restart button bypassed the protected container list. Added `is_protected()` checks (P0-2, P0-3)
- **Sanitized error messages** - Raw exception details from Docker SDK no longer leak to Telegram users (P1-6)
- **ReDoS prevention** - Added signal-based regex timeout for AI-generated ignore patterns (P1-14)
- **Docker socket security** - Documented root-equivalent access risk, added `docker-socket-proxy` recommendation (P1-19)

### Fixed
- **`pull_and_recreate()` redesigned with rollback** - Previously deleted the container before recreation with no recovery path and only extracted 5 config properties. Now preserves 30+ properties and rolls back on failure (P0-1)
- **Wrong method calls in manage dashboard** - `unmute()` → `unmute_array()` and `remove_mute("server")` → `unmute_server()` fixed, preventing partial unmutes and runtime crashes (P0-5, P0-8)
- **NL tool array status schema mismatch** - Was checking wrong field names (`status == "healthy"`, `used_bytes`), now matches actual GraphQL schema (`DISK_OK`, `capacity.kilobytes`) (P0-6)
- **CPU temperature always returned 0** - Changed from hardcoded `0` to `None` when unavailable, with graceful handling in display and alert code (P0-7)
- **Async Anthropic client** - All three Claude API callers switched from synchronous to async, no longer blocking the event loop for 2-30 seconds per call (P1-1)
- **Async Docker SDK calls** - Blocking Docker calls wrapped in `asyncio.to_thread()` across 4 files (P1-2)
- **Parallel Docker stats collection** - `get_all_stats()` now uses `asyncio.gather()` instead of sequential calls, reducing poll time from 20-40s to ~2s for 20 containers (P1-3)
- **Thread-unsafe asyncio.Queue** - Fixed with `call_soon_threadsafe()` in log watcher and docker events (P1-4)
- **Default config uses HTTPS** - Changed default Unraid connection from HTTP port 80 to HTTPS port 443 (P1-5)
- **Alert queuing before chat ID** - Alerts during startup are now queued and flushed when first user sends `/start` (P1-7)
- **Null safety for `from_user` and `callback.message`** - Added None guards preventing AttributeError in channel posts (P1-8, P1-9)
- **Unraid system monitor rate limiting** - No longer sends duplicate alerts every 30-second poll cycle (P1-10)
- **Cancellable log watcher threads** - Stream close mechanism prevents orphaned threads on container restarts (P1-11)
- **Async locking for mute/ignore managers** - Added `asyncio.Lock` to prevent race conditions (P1-12)
- **Unraid client HTTP timeout** - Set 10-second timeout, preventing 5-minute stalls on unresponsive server (P1-13)
- **Hardcoded IP removed** - Replaced `192.168.0.190` with `your-unraid-ip` placeholder in config template (P1-15)
- **Running containers filter** - `containers.list()` now filters by status instead of fetching all (P1-18)
- **Memory leak fixes** - TTL-based cleanup for rate_limiter, diagnostic pending dict, confirmation manager, ignore/manage selection states (P2-10 through P2-13)
- **Threading/task bugs** - Removed threading.Lock from async memory_monitor, fixed double task creation in system_monitor, added ghost container cleanup on reconnect (P2-14 through P2-16)
- **Telegram callback data overflow** - UTF-8 byte-length calculation for callback_data, colon-safe splitting, markdown escaping for container names (P2-17, P2-18, P2-26)
- **Log truncation** - Now accounts for header/footer length to stay within Telegram's 4096 char limit (P2-27)

### Changed
- **Dependencies fully declared** - Added all missing runtime deps (aiogram, pyyaml, pydantic, anthropic, psutil, aiohttp, python-dotenv) to pyproject.toml with pinned upper bounds (P1-16, P1-17, P2-19, P2-25)
- **Dockerfile base image** - Added digest pinning comment (P2-20)
- **Config template** - Added `panic` and `traceback` to default error_patterns (P2-22)
- **`unraid-api` version constraint** - Updated from `>=0.1.0,<1.0.0` to `>=1.0.0,<2.0.0` to match available releases

### Removed
- Dead code: unused imports (`asdict`, `Bot`), dead functions (`cancel`, `is_action_tool`, `is_read_only_tool`, `get_vms`, `get_ups_status`), unused variables, empty `__init__` re-exports (P3-1 through P3-9)
- Orphan UPS config fields with no UPS monitor (P3-12 through P3-14)
- Duplicate `format_uptime` and `extract_container_from_alert` implementations, consolidated into shared utils (P3-10, P3-11)

### Added
- 71 new tests covering ContainerController, DockerEventMonitor, AlertManagerProxy, control commands, NLProcessor, MemoryStore, LogWatcher, ConfirmationManager, AlertManager (P2-1 through P2-9)
- Comprehensive codebase audit report (`docs/audit-report-2026-02-06.md`)
- Shared `utils/formatting.py` module for consolidated utility functions

## [0.7.2] - 2026-02-01

### Fixed
- **Missing resource_monitoring in default config** - The auto-generated config.yaml was missing the resource_monitoring section, causing new deployments to use hardcoded defaults instead of configurable values.

### Added
- `.dockerignore` file to exclude config, tests, and dev files from Docker images
- Tests for default config generation (4 new tests verifying YAML validity and section loading)

### Security
- Added `config/config.yaml` to `.gitignore` to prevent accidental commit of user configurations

## [0.7.1] - 2026-02-01

### Fixed
- **High CPU usage from regex ignore patterns** - Regex patterns were being compiled on every log line check, causing 90%+ sustained CPU. Now pre-compiled once when pattern is created.
- Added logging to bare exception handlers in docker_events.py (previously silent failures)
- Added JSONDecodeError handling in Unraid GraphQL client
- Added debug logging for Docker timestamp parsing failures
- Removed unused `monitoring.health_check_interval` config option

### Changed
- Updated CLAUDE.md with accurate environment variables and architecture documentation
- Updated README.md storage section (removed non-existent database reference)

### Added
- Test coverage for alert_callbacks.py (30 tests) - restart, logs, diagnose, mute button handlers
- Test coverage for BaseMuteManager (25 tests) - JSON persistence, expiry logic, edge cases
- Total test count: 502 (up from 447)

## [0.7.0] - 2026-01-28

### Added
- `/manage` command - dashboard with quick access buttons for:
  - Container status overview
  - Resource usage summary
  - Manage runtime ignores (view and remove)
  - Manage active mutes (view and remove container, server, and array mutes)

## [0.6.0] - 2025-01-27

### Added
- Quick action buttons on all alerts (Restart, Logs, Diagnose, Mute)
- Memory pressure management with automatic container killing
- Smart ignore pattern generation using AI (Claude Haiku)
- Persistent storage for mutes and ignore patterns
- `/cancel-kill` command to abort pending memory pressure kills

### Changed
- Error alerts now show "Ignore Similar" button with AI-powered pattern extraction
- Crash alerts include Restart button for one-tap recovery
- All alerts include Mute buttons (1h and 24h options)

## [0.5.0] - 2025-01-26

### Added
- Unraid server monitoring via GraphQL API
- `/server` and `/server detailed` commands for system metrics
- `/array` and `/disks` commands for array/disk status
- Server temperature, memory, and UPS alerts
- Array health monitoring (disk temps, SMART status, parity)
- `/mute-server` and `/mute-array` commands
- Array mute manager for disk/parity alerts

## [0.4.0] - 2025-01-25

### Added
- `/mute` and `/unmute` commands for container alert control
- `/mutes` command to view all active mutes
- `/ignore` command to create ignore patterns from recent errors
- `/ignores` command to list all ignore patterns
- Recent errors buffer for ignore pattern selection
- Persistent mute storage in JSON files

## [0.3.0] - 2025-01-24

### Added
- Resource monitoring with CPU/memory threshold alerts
- `/resources` command for container resource stats
- Per-container threshold configuration
- Sustained threshold checking (alerts after duration exceeded)

### Changed
- Alerts now include resource context (memory/CPU usage)

## [0.2.0] - 2025-01-23

### Added
- AI-powered diagnostics with `/diagnose` command
- Log watching with configurable error patterns
- Log error alerts with rate limiting
- Container control commands (`/restart`, `/stop`, `/start`, `/pull`)
- Protected containers list to prevent accidental control
- Confirmation prompts for destructive actions

## [0.1.0] - 2025-01-22

### Added
- Initial release
- Docker container monitoring via socket
- Crash detection with exit code interpretation
- `/status` and `/logs` commands
- Telegram bot with user authentication
- Basic alert system for container events
