"""Bot health and status command."""

import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from src.monitors.docker_events import DockerEventMonitor
    from src.monitors.log_watcher import LogWatcher
    from src.monitors.resource_monitor import ResourceMonitor
    from src.monitors.memory_monitor import MemoryMonitor
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.unraid.monitors.array_monitor import ArrayMonitor
    from src.unraid.client import UnraidClientWrapper

logger = logging.getLogger(__name__)

# Version is updated manually or via CI
BOT_VERSION = "0.8.2"


def _format_health_uptime(start_time: datetime) -> str:
    """Format bot uptime from start time to now."""
    delta = datetime.now(timezone.utc) - start_time
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def health_command(
    start_time: datetime,
    monitor: "DockerEventMonitor | None" = None,
    log_watcher: "LogWatcher | None" = None,
    resource_monitor: "ResourceMonitor | None" = None,
    memory_monitor: "MemoryMonitor | None" = None,
    unraid_client: "UnraidClientWrapper | None" = None,
    unraid_system_monitor: "UnraidSystemMonitor | None" = None,
    unraid_array_monitor: "ArrayMonitor | None" = None,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /health command handler."""

    async def handler(message: Message) -> None:
        uptime = _format_health_uptime(start_time)

        lines = [
            f"🏥 *Bot Health*",
            f"",
            f"*Version:* {BOT_VERSION}",
            f"*Uptime:* {uptime}",
            f"",
            f"*Monitors:*",
        ]

        # Docker event monitor
        if monitor:
            status = "✅ Running" if monitor._running else "🔴 Stopped"
            container_count = len(monitor.state_manager.get_all())
            lines.append(f"  Docker Events: {status} ({container_count} containers)")
        else:
            lines.append("  Docker Events: ⚪ Not configured")

        # Log watcher
        if log_watcher:
            status = "✅ Running" if log_watcher._running else "🔴 Stopped"
            watched = len(log_watcher.containers)
            lines.append(f"  Log Watcher: {status} ({watched} containers)")
        else:
            lines.append("  Log Watcher: ⚪ Not configured")

        # Resource monitor
        if resource_monitor:
            status = "✅ Running" if resource_monitor._running else "🔴 Stopped"
            lines.append(f"  Resources: {status}")
        else:
            lines.append("  Resources: ⚪ Disabled")

        # Memory monitor
        if memory_monitor:
            status = "✅ Running" if memory_monitor._running else "🔴 Stopped"
            lines.append(f"  Memory: {status}")
        else:
            lines.append("  Memory: ⚪ Disabled")

        # Unraid
        if unraid_client:
            connected = "✅ Connected" if unraid_client.is_connected else "🔴 Disconnected"
            lines.append(f"  Unraid: {connected}")
            if unraid_system_monitor:
                status = "✅" if unraid_system_monitor._running else "🔴"
                lines.append(f"    System: {status}")
            if unraid_array_monitor:
                status = "✅" if unraid_array_monitor._running else "🔴"
                lines.append(f"    Array: {status}")
        else:
            lines.append("  Unraid: ⚪ Not configured")

        # Crash tracker stats
        if monitor:
            tracker = monitor._crash_tracker
            active_loops = []
            for name, crashes in tracker._crashes.items():
                count = tracker.get_crash_count(name)
                if count >= 3:
                    active_loops.append(f"{name} ({count}x)")
            if active_loops:
                lines.append("")
                lines.append("*Recent Crashes:*")
                for item in active_loops:
                    lines.append(f"  ⚠️ {item}")

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler
