import asyncio
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message
import docker

from src.models import ContainerInfo
from src.state import ContainerStateManager
from src.utils.formatting import format_bytes, format_uptime
from src.utils.sanitize import sanitize_logs_for_display
from src.bot.resources_command import format_progress_bar

if TYPE_CHECKING:
    from src.monitors.resource_monitor import ResourceMonitor

logger = logging.getLogger(__name__)


HELP_TEXT = """📋 *Commands*

*Containers*
/status [name] • /resources [name]
/logs <name> [n] • /diagnose <name>
/restart • /stop • /start • /pull

*Unraid Server*
/server [detailed] • /array • /disks

*Memory Management*
/cancel-kill • Cancel pending container kill

*Alerts & Ignores*
/mute <name> <dur> • /unmute <name>
/mute-server • /mute-array + unmute
/mutes • /ignore • /ignores

*Quick Access*
/manage • Dashboard with status, resources, ignores & mutes

_Partial names work: /status rad → radarr_
_Reply /diagnose to crash alerts for AI analysis_
_Click "Ignore Similar" on alerts for smart patterns_"""


def help_command(state: ContainerStateManager) -> Callable[[Message], Awaitable[None]]:
    """Factory for /help command handler."""
    async def handler(message: Message) -> None:
        await message.answer(HELP_TEXT, parse_mode="Markdown")
    return handler


def format_status_summary(state: ContainerStateManager) -> str:
    """Format container status summary."""
    summary = state.get_summary()
    all_containers = state.get_all()

    stopped = [c.name for c in all_containers if c.status != "running"]
    unhealthy = [c.name for c in all_containers if c.health == "unhealthy"]

    lines = [
        "📊 *Container Status*",
        "",
        f"✅ Running: {summary['running']}",
        f"🔴 Stopped: {summary['stopped']}",
        f"⚠️ Unhealthy: {summary['unhealthy']}",
    ]

    if stopped:
        lines.append("")
        lines.append(f"*Stopped:* {', '.join(stopped)}")

    if unhealthy:
        lines.append(f"*Unhealthy:* {', '.join(unhealthy)}")

    if not stopped and not unhealthy:
        lines.append("")
        lines.append("_All containers healthy_ ✨")
    else:
        lines.append("")
        lines.append("_Use /status <name> for details_")

    return "\n".join(lines)


async def format_container_details(
    container: ContainerInfo,
    resource_monitor: "ResourceMonitor | None" = None,
) -> str:
    """Format detailed container info with optional resource stats."""
    health_emoji = {
        "healthy": "✅",
        "unhealthy": "⚠️",
        "starting": "🔄",
        None: "➖",
    }
    status_emoji = "🟢" if container.status == "running" else "🔴"

    lines = [
        f"*{container.name}*",
        "",
        f"Status: {status_emoji} {container.status}",
        f"Health: {health_emoji.get(container.health, '➖')} {container.health or 'no healthcheck'}",
    ]

    # Show uptime for running containers
    if container.status == "running" and container.uptime_seconds is not None:
        lines.append(f"Uptime: {format_uptime(container.uptime_seconds)}")

    lines.append(f"Image: `{container.image}`")

    if container.started_at:
        lines.append(f"Started: {container.started_at.strftime('%Y-%m-%d %H:%M:%S')}")

    # Add resource stats for running containers
    if container.status == "running" and resource_monitor is not None:
        try:
            stats = await resource_monitor.get_container_stats(container.name)
            if stats is not None:
                cpu_bar = format_progress_bar(stats.cpu_percent)
                mem_bar = format_progress_bar(stats.memory_percent)

                lines.append("")
                lines.append("📊 *Resources*")
                lines.append(
                    f"CPU:    {stats.cpu_percent:5.1f}% `{cpu_bar}`"
                )
                lines.append(
                    f"Memory: {stats.memory_percent:5.1f}% `{mem_bar}` "
                    f"({stats.memory_display} / {stats.memory_limit_display})"
                )
                lines.append(
                    f"Net I/O: {format_bytes(stats.net_rx_bytes)} ↓ / "
                    f"{format_bytes(stats.net_tx_bytes)} ↑"
                )
                lines.append(
                    f"Block I/O: {format_bytes(stats.block_read_bytes)} read / "
                    f"{format_bytes(stats.block_write_bytes)} write"
                )
                if stats.pids > 0:
                    lines.append(f"PIDs: {stats.pids}")
        except Exception as e:
            logger.warning(f"Failed to get resource stats for {container.name}: {e}")

    return "\n".join(lines)


def status_command(
    state: ContainerStateManager,
    resource_monitor: "ResourceMonitor | None" = None,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /status command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split(maxsplit=1)

        if len(parts) == 1:
            # No argument - show summary
            response = format_status_summary(state)
        else:
            # Search for container
            query = parts[1].strip()
            matches = state.find_by_name(query)

            if not matches:
                response = f"❌ No container found matching '{query}'"
            elif len(matches) == 1:
                response = await format_container_details(matches[0], resource_monitor)
            else:
                names = ", ".join(m.name for m in matches)
                response = f"Multiple matches found: {names}\n\n_Be more specific_"

        await message.answer(response, parse_mode="Markdown")

    return handler


def logs_command(
    state: ContainerStateManager,
    docker_client: docker.DockerClient,
    max_lines: int = 100,
    max_chars: int = 4000,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /logs command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /logs <container> [lines]\n\nExample: /logs radarr 50")
            return

        container_name = parts[1]

        # Parse optional line count
        try:
            lines = int(parts[2]) if len(parts) > 2 else 20
        except ValueError:
            lines = 20

        # Cap at reasonable limit
        lines = min(lines, max_lines)

        # Find container
        matches = state.find_by_name(container_name)

        if not matches:
            await message.answer(f"❌ No container found matching '{container_name}'")
            return

        if len(matches) > 1:
            names = ", ".join(m.name for m in matches)
            await message.answer(f"Multiple matches found: {names}\n\n_Be more specific_", parse_mode="Markdown")
            return

        container = matches[0]

        try:
            docker_container = await asyncio.to_thread(docker_client.containers.get, container.name)
            log_bytes = await asyncio.to_thread(docker_container.logs, tail=lines, timestamps=False)
            log_text = log_bytes.decode("utf-8", errors="replace")

            # Truncate if too long for Telegram
            if len(log_text) > max_chars:
                log_text = log_text[-max_chars:]
                log_text = "...(truncated)\n" + log_text

            # Sanitize to remove sensitive data before display
            log_text = sanitize_logs_for_display(log_text)

            response = f"📋 *Logs: {container.name}* (last {lines} lines)\n\n```\n{log_text}\n```"
            await message.answer(response, parse_mode="Markdown")

        except docker.errors.NotFound:
            await message.answer(f"❌ Container '{container.name}' not found in Docker")
        except Exception as e:
            await message.answer(f"❌ Error getting logs. Check bot logs for details.")

    return handler
