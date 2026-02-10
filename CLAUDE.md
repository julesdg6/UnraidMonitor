# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Unraid Server Monitor Bot (v0.8.1) - A Docker-based Telegram bot for monitoring Unraid servers. Monitors Docker containers (events, logs, resources) and Unraid server health (CPU, memory, disks, array, UPS). Uses Claude API for AI-powered diagnostics and natural language interaction. Sends alerts via Telegram with quick-action buttons.

## Commands

```bash
# Run the application
python -m src.main

# Run tests (uses pytest-asyncio with auto mode)
pytest tests/
pytest tests/test_<module>.py
pytest tests/test_<module>.py -k "test_name"
pytest --cov=src tests/

# Type checking (strict mode, Python 3.11)
mypy src/

# Linting (line-length 100, target py311)
ruff check src/

# Docker (target is Unraid x86_64 -- always build for linux/amd64)
docker buildx build --platform linux/amd64 -t dervish/unraidmonitorbot:latest --push .
docker-compose up -d
```

## Architecture

### Data Flow
```
Docker Socket ──→ DockerEventMonitor ──→ AlertManagerProxy ──→ Telegram Bot ──→ User
                  LogWatcher ──────────→ (RateLimiter,       ↗                   ↓
                  ResourceMonitor ─────→  MuteManager,      /              Docker Actions
                                          IgnoreManager)   /
Unraid API ────→ UnraidSystemMonitor ──→ AlertManagerProxy/
                 ArrayMonitor ─────────→
```

### Startup & Wiring (`src/main.py`)

`main.py` is the composition root. It instantiates all components and wires them together:
- **First-run path:** If no `config.yaml` exists, starts the setup wizard which guides users through Unraid connection and container classification via Telegram, then restarts via `os.execv`
- **Normal path:** Loads config and starts all monitors immediately
- `AlertManagerProxy` wraps `AlertManager` to lazily resolve the Telegram chat ID (set on first `/start` command)
- Background tasks for each monitor run concurrently via `asyncio.create_task`
- Telegram bot uses aiogram 3.x polling
- Components are passed to bot command handlers via aiogram's dependency injection

### Key Modules

**Monitors** (`src/monitors/`) - Passive observers that emit alerts:
- `docker_events.py` - Docker socket subscription (die, start, health_status, oom events) + `CrashTracker` for restart loop detection
- `log_watcher.py` - Streams container logs, matches error patterns, applies ignore rules
- `resource_monitor.py` - Periodic CPU/memory polling per container
- `memory_monitor.py` - System-level memory pressure management (can kill containers)

**Unraid** (`src/unraid/`) - Unraid server integration:
- `client.py` - GraphQL API wrapper using `unraid-api` package
- `monitors/system_monitor.py` - CPU temp, usage, memory with configurable thresholds
- `monitors/array_monitor.py` - Disk health, array usage, temperature monitoring

**Bot** (`src/bot/`) - Telegram command/callback handlers:
- `telegram_bot.py` - Bot/dispatcher setup, handler registration
- `commands.py` - `/status`, `/logs`, `/help`
- `control_commands.py` - `/restart`, `/stop`, `/start`, `/pull` with confirmations
- `diagnose_command.py` - `/diagnose` with AI-powered log analysis
- `resources_command.py` - `/resources` with per-container stats
- `unraid_commands.py` - `/server`, `/array`, `/disks`
- `mute_command.py` - `/mute`, `/unmute`, `/mutes`
- `ignore_command.py` - `/ignore`, `/ignores` (AI-generated patterns)
- `manage_command.py` - `/manage` dashboard with inline keyboards
- `alert_callbacks.py` - Quick-action button handlers on alert messages
- `nl_handler.py` - Routes non-command text to NL processor
- `health_command.py` - `/health` bot version, uptime, and monitor status
- `setup_wizard.py` - Interactive first-run setup wizard and `/setup` re-run support

**Services** (`src/services/`) - Business logic:
- `nl_processor.py` - Natural language chat via Claude with tool use and conversation memory
- `nl_tools.py` - Tool definitions (get status, read logs, restart) for Claude tool use
- `container_control.py` - Safe container operations (name matching, protected list)
- `container_classifier.py` - Pattern + AI classification of containers into categories
- `diagnostic.py` - AI log analysis (brief/detailed modes)

**Alerts** (`src/alerts/`) - Alert management layer:
- `manager.py` + `AlertManagerProxy` (in main.py) - Format and send Telegram messages
- `rate_limiter.py` - Deduplication with configurable cooldowns
- `mute_manager.py`, `server_mute_manager.py`, `array_mute_manager.py` - Timed/permanent mutes (JSON persistence in `data/`)
- `ignore_manager.py` - Regex-based error pattern ignoring (JSON persistence in `data/`)
- `recent_errors.py` - Buffer for `/ignore` command's error selection

**Analysis** (`src/analysis/`):
- `pattern_analyzer.py` - Uses Claude to generate smart ignore patterns from error examples

### Patterns

- **All async** - Every component uses async/await. Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- **Partial name matching** - Container commands accept partial names (e.g., `/logs rad` matches `radarr`)
- **Graceful degradation** - Bot works without `ANTHROPIC_API_KEY`; AI features just disable
- **JSON persistence** - Mutes and ignores stored in `data/*.json` files
- **Protected containers** - Listed in `config.yaml`, cannot be controlled via Telegram
- **Confirmation prompts** - Destructive actions (restart, stop, pull) require button confirmation
- **Europe/London timezone** - All displayed timestamps use this timezone

## Environment Variables

```bash
TELEGRAM_BOT_TOKEN=           # Required - from @BotFather
TELEGRAM_ALLOWED_USERS=       # Required - comma-separated Telegram user IDs (e.g., 123456,789012)
ANTHROPIC_API_KEY=            # Optional - enables AI diagnostics, NL chat, smart ignore
UNRAID_API_KEY=               # Optional - enables /server, /array, /disks commands
CONFIG_PATH=                  # Optional - defaults to config/config.yaml
LOG_LEVEL=                    # Optional - defaults to INFO
```

## Configuration

`config/config.yaml` - Created by the setup wizard on first run (or via `/setup`). Key sections:
- `ai` - Claude model names, token limits, NL processor settings
- `log_watching` - Watched containers, error/ignore patterns, cooldown
- `unraid` - Host, polling intervals, alert thresholds (CPU temp, disk temp, memory, etc.)
- `protected_containers` / `ignored_containers` - Safety and visibility controls
- `memory_management` - System memory pressure thresholds and kill policy
- `resource_monitoring` - Per-container CPU/memory alert thresholds
