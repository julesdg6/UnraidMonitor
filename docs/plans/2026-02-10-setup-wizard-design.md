# Setup Wizard Design

Telegram-based onboarding wizard that replaces the current "generate default config.yaml" approach with a conversational setup flow.

## Problem

On first run, the bot generates a default `config.yaml` with empty container lists and a placeholder Unraid host. Users must SSH into their server and edit YAML manually before the bot is useful. This is friction-heavy, especially since the bot already has access to Docker and (optionally) Unraid at startup.

## Flow

```
First run (no config.yaml)
    │
    ▼
Bot starts in SETUP MODE (monitors paused, only wizard handlers active)
    │
    ▼
User sends /start
    │
    ▼
Step 1: Welcome message, explain quick setup
    │
    ▼
Step 2: Unraid connection (if UNRAID_API_KEY set)
    │  - Ask for IP/hostname
    │  - Auto-detect port/SSL (try 443+SSL, then 80+HTTP)
    │  - Report success or ask for manual port
    │
    ▼
Step 3: Container discovery & categorization
    │  - Fetch all containers from Docker
    │  - Pattern-match known software (databases, *arr, media, etc.)
    │  - Batch remaining unknowns to Haiku for AI categorization (if ANTHROPIC_API_KEY set)
    │  - Present grouped summary with inline adjustment buttons
    │
    ▼
Step 4: User confirms or adjusts
    │  - Toggle containers on/off per category via inline keyboards
    │  - Conflict resolution (e.g. can't be both ignored and watched)
    │
    ▼
Step 5: Save config.yaml, start all monitors, deliver queued alerts
```

## State Machine

```
IDLE → AWAITING_HOST → CONNECTING → REVIEW_CONTAINERS → ADJUSTING → SAVING → COMPLETE
```

State is per-user (keyed by Telegram user ID), stored in memory. No persistence needed since setup is one-shot. Bot restart mid-wizard simply restarts the wizard (no config saved yet).

## Smart Default Categorization

### Pattern Matching (first pass)

| Pattern | Category |
|---|---|
| `mariadb`, `mysql`, `postgresql`, `postgres`, `redis`, `mongodb`, `influxdb`, `couchdb` | priority + watched |
| `unraid-monitor-bot` | protected (always) |
| `plex`, `emby`, `jellyfin`, `tautulli` | watched |
| `radarr`, `sonarr`, `lidarr`, `readarr`, `prowlarr`, `bazarr` | watched |
| `overseerr`, `ombi` | watched |
| `qbittorrent`, `qbit`, `sabnzbd`, `sab`, `nzbget`, `deluge`, `transmission` | watched + killable candidate |

### AI Categorization (second pass)

Unmatched containers are sent to Haiku in a single batch call with container name, image name, and status. Haiku returns a suggested category and brief description for each.

AI-suggested containers are marked with `*` in the summary so the user knows which were inferred.

Fallback: if no `ANTHROPIC_API_KEY`, unknowns go to "unassigned" for manual categorization.

## Telegram UX

### Summary View

```
Here's what I'd suggest:

  Priority: mariadb, postgresql14, redis, authelia*
  Protected: unraid-monitor-bot
  Watched: plex, radarr, sonarr, overseerr, bookstack*
  Killable candidates: qbit, sab
  Ignored: dozzle*, kometa*

* = AI-suggested

[Adjust Priority] [Adjust Watched] [Adjust Killable]
[Adjust Ignored] [Adjust Protected]
[Looks Good]
```

### Adjustment View (per category)

```
Adjust Watched Containers:
[plex ON] [radarr ON] [sonarr ON]
[bookstack* ON] [nginx-proxy OFF]
[watchtower OFF] [authelia OFF]
[Done]
```

Tapping a container toggles on/off. "Done" returns to summary with updates.

### Unraid Connection

```
Let's connect to your Unraid server.
What's the IP address or hostname?
```

User types IP. Bot auto-detects port/SSL. On success shows server name and version. On failure asks for manual port.

### Completion

```
Setup complete! Monitoring is now active.

Watching logs: 8 containers
Priority: 3 containers
Protected: 1 container
Unraid: connected (192.168.0.190)

Use /manage for a dashboard, or /help for all commands.
```

## Re-running via /setup

The `/setup` command triggers the same wizard but pre-fills current config values. The config writer merges changes: only container roles and Unraid connection are updated. Thresholds, AI config, error patterns, and other manual tweaks are preserved.

Containers that no longer exist are removed silently. New containers appear as unassigned.

## Implementation Structure

### New Files

- `src/bot/setup_wizard.py` - state machine, Telegram handlers, UX flow
- `src/services/container_classifier.py` - pattern matching + Haiku batch call

### Modified Files

- `src/main.py` - detect first-run, start in setup mode, trigger monitor startup after wizard completes
- `src/config.py` - add `ConfigWriter` with `write()` and `merge()` methods alongside existing `generate_default_config()`
- `src/bot/telegram_bot.py` - register `/setup` command, setup-mode middleware

### Integration with main.py

On first run, `main.py` starts the bot and Docker connection (needed to list containers) but skips starting monitors. A `setup_mode=True` flag is passed. The wizard registers its own handlers. Once complete, it triggers monitor startup without requiring a restart.

During setup mode, a middleware intercepts all messages and routes to the wizard. Only `/help` bypasses it.

## Scope

### In scope

- Setup wizard flow (first-run + /setup re-run)
- Container classifier (patterns + Haiku)
- Config writer with merge support
- Setup-mode middleware
- Inline keyboard UX for container toggling

### Out of scope

- Threshold configuration in wizard (use defaults)
- AI model selection in wizard
- Error pattern customization in wizard
- Memory management enable/disable in wizard
- Multi-user wizard setup (first allowed user drives it)

## Edge Cases

- No `ANTHROPIC_API_KEY`: pattern matching only, unknowns shown as unassigned
- No `UNRAID_API_KEY`: skip Unraid step, set `unraid.enabled: false`
- Zero Docker containers: skip categorization, save config with empty lists
- Bot restart mid-wizard: no config saved yet, wizard restarts cleanly
- `/setup` re-run with removed containers: cleaned from config, new ones shown as unassigned
- Mutually exclusive categories: adding to "ignored" auto-removes from "watched" (and vice versa), bot states the change briefly
