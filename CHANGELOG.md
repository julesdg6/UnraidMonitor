# Changelog

All notable changes to UnraidMonitor will be documented in this file.

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
