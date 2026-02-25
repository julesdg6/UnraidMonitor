import logging
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


from src.utils.formatting import format_bytes, format_uptime, strip_log_timestamps
from src.utils.telegram_retry import send_with_retry

logger = logging.getLogger(__name__)


class ChatIdStore:
    """In-memory storage for alert chat IDs (supports multiple users)."""

    def __init__(self):
        self._chat_ids: set[int] = set()

    def set_chat_id(self, chat_id: int) -> None:
        """Add a chat ID to the set."""
        self._chat_ids.add(chat_id)

    def get_chat_id(self) -> int | None:
        """Get any stored chat ID (backward compat)."""
        return next(iter(self._chat_ids), None)

    def get_all_chat_ids(self) -> set[int]:
        """Get all stored chat IDs."""
        return set(self._chat_ids)


class AlertManager:
    """Manages sending alerts to Telegram."""

    def __init__(self, bot: Bot, chat_id: int, error_display_max_chars: int = 200):
        self.bot = bot
        self.chat_id = chat_id
        self.error_display_max_chars = error_display_max_chars

    async def send_crash_alert(
        self,
        container_name: str,
        exit_code: int,
        image: str,
        uptime_seconds: int | None = None,
        restart_loop_count: int | None = None,
    ) -> None:
        """Send a container crash alert with quick action buttons."""
        uptime_str = format_uptime(uptime_seconds) if uptime_seconds else "unknown"

        # Interpret common exit codes
        exit_reason = ""
        if exit_code == 137:
            exit_reason = " (OOM killed)"
        elif exit_code == 143:
            exit_reason = " (SIGTERM)"
        elif exit_code == 139:
            exit_reason = " (segfault)"

        if restart_loop_count:
            text = f"""🔄🔴 *RESTART LOOP:* {container_name}

Crashed {restart_loop_count} times in the last 10 minutes!
Exit code: {exit_code}{exit_reason}
Image: `{image}`"""
        else:
            text = f"""🔴 *CONTAINER CRASHED:* {container_name}

Exit code: {exit_code}{exit_reason}
Image: `{image}`
Uptime: {uptime_str}"""

        # Quick action buttons
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Restart", callback_data=f"restart:{container_name}"),
                    InlineKeyboardButton(text="📋 Logs", callback_data=f"logs:{container_name}:50"),
                    InlineKeyboardButton(text="🔍 Diagnose", callback_data=f"diagnose:{container_name}"),
                ],
                [
                    InlineKeyboardButton(text="🔕 Mute 1h", callback_data=f"mute:{container_name}:60"),
                    InlineKeyboardButton(text="🔕 Mute 24h", callback_data=f"mute:{container_name}:1440"),
                ],
            ]
        )

        try:
            await send_with_retry(
                self.bot.send_message,
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            logger.info(f"Sent crash alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send crash alert: {e}")

    async def send_log_error_alert(
        self,
        container_name: str,
        error_line: str,
        suppressed_count: int = 0,
    ) -> None:
        """Send a log error alert with ignore button."""
        total_errors = suppressed_count + 1

        # Truncate long error lines for display
        display_error = error_line
        if len(error_line) > self.error_display_max_chars:
            display_error = error_line[:self.error_display_max_chars] + "..."

        if total_errors > 1:
            count_text = f"Found {total_errors} errors in the last 15 minutes"
        else:
            count_text = "New error detected"

        text = f"""⚠️ *ERRORS IN:* {container_name}

{count_text}

Latest: `{display_error}`

/logs {container_name} 50 - View last 50 lines"""

        # Create inline keyboard with quick action buttons
        # Telegram limits callback_data to 64 bytes (UTF-8 encoded)
        prefix = f"ignore_similar:{container_name}:"
        # Strip timestamps so more meaningful content fits in 64 bytes
        error_preview = strip_log_timestamps(error_line)
        while len(f"{prefix}{error_preview}".encode("utf-8")) > 64:
            error_preview = error_preview[:-1]

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔇 Ignore Similar", callback_data=f"{prefix}{error_preview}"),
                    InlineKeyboardButton(text="🔕 Mute 1h", callback_data=f"mute:{container_name}:60"),
                ],
                [
                    InlineKeyboardButton(text="📋 Logs", callback_data=f"logs:{container_name}:50"),
                    InlineKeyboardButton(text="🔍 Diagnose", callback_data=f"diagnose:{container_name}"),
                ],
            ]
        )

        try:
            await send_with_retry(
                self.bot.send_message,
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            logger.info(f"Sent log error alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send log error alert: {e}")

    async def send_resource_alert(
        self,
        container_name: str,
        metric: str,
        current_value: float,
        threshold: int,
        duration_seconds: int,
        memory_bytes: int,
        memory_limit: int,
        memory_percent: float,
        cpu_percent: float,
    ) -> None:
        """Send a resource threshold alert.

        Args:
            container_name: Container name.
            metric: "cpu" or "memory".
            current_value: Current metric value.
            threshold: Threshold that was exceeded.
            duration_seconds: How long threshold has been exceeded.
            memory_bytes: Current memory usage in bytes.
            memory_limit: Memory limit in bytes.
            memory_percent: Memory usage percentage.
            cpu_percent: CPU usage percentage.
        """
        duration_str = self._format_duration(duration_seconds)
        memory_display = format_bytes(memory_bytes)
        memory_limit_display = format_bytes(memory_limit)

        if metric == "cpu":
            title = "HIGH RESOURCE USAGE"
            primary = f"CPU: {current_value}% (threshold: {threshold}%)"
            secondary = f"Memory: {memory_display} / {memory_limit_display} ({memory_percent}%)"
        else:
            title = "HIGH MEMORY USAGE"
            primary = f"Memory: {current_value}% (threshold: {threshold}%)"
            primary += f"\n        {memory_display} / {memory_limit_display} limit"
            secondary = f"CPU: {cpu_percent}% (normal)"

        text = f"""⚠️ *{title}:* {container_name}

{primary}
Exceeded for: {duration_str}

{secondary}"""

        # Quick action buttons
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📋 Logs", callback_data=f"logs:{container_name}:50"),
                    InlineKeyboardButton(text="🔍 Diagnose", callback_data=f"diagnose:{container_name}"),
                ],
                [
                    InlineKeyboardButton(text="🔕 Mute 1h", callback_data=f"mute:{container_name}:60"),
                    InlineKeyboardButton(text="🔕 Mute 24h", callback_data=f"mute:{container_name}:1440"),
                ],
            ]
        )

        try:
            await send_with_retry(
                self.bot.send_message,
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            logger.info(f"Sent resource alert for {container_name} ({metric})")
        except Exception as e:
            logger.error(f"Failed to send resource alert: {e}")

    async def send_recovery_alert(self, container_name: str) -> None:
        """Send a brief recovery notification when a crashed container starts."""
        text = f"✅ *{container_name}* recovered and is running again."

        try:
            await send_with_retry(
                self.bot.send_message,
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info(f"Sent recovery alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send recovery alert: {e}")

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Format duration in human-readable form."""
        if seconds >= 3600:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
        minutes = seconds // 60
        if minutes > 0:
            return f"{minutes} minutes" if minutes > 1 else "1 minute"
        return f"{seconds} seconds"
