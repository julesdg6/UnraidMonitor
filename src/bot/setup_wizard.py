"""Setup wizard for first-run configuration.

Guides users through Unraid connection, container classification,
and config generation via an interactive Telegram conversation.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, TYPE_CHECKING

import aiohttp
from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.config import ConfigWriter
from src.services.container_classifier import (
    ContainerClassification,
    ContainerClassifier,
    VALID_CATEGORIES,
)

if TYPE_CHECKING:
    import anthropic
    import docker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------

class WizardState(Enum):
    IDLE = "idle"
    AWAITING_HOST = "awaiting_host"
    CONNECTING = "connecting"
    REVIEW_CONTAINERS = "review_containers"
    ADJUSTING = "adjusting"
    SAVING = "saving"
    COMPLETE = "complete"


# ---------------------------------------------------------------------------
# Per-user session
# ---------------------------------------------------------------------------

@dataclass
class WizardSession:
    state: WizardState = WizardState.IDLE
    unraid_host: str | None = None
    unraid_port: int = 80
    unraid_use_ssl: bool = False
    classifications: list[ContainerClassification] = field(default_factory=list)
    adjusting_category: str | None = None


# ---------------------------------------------------------------------------
# Category display helpers
# ---------------------------------------------------------------------------

_CATEGORY_EMOJI = {
    "priority": "\u2b50",     # star
    "protected": "\U0001f6e1\ufe0f",  # shield
    "watched": "\U0001f440",  # eyes
    "killable": "\U0001f4a5",  # collision
    "ignored": "\U0001f6ab",  # prohibited
}

_CATEGORY_LABELS = {
    "priority": "Priority (never kill)",
    "protected": "Protected (no remote control)",
    "watched": "Watched (monitor logs)",
    "killable": "Killable (can free memory)",
    "ignored": "Ignored (not monitored)",
}


# ---------------------------------------------------------------------------
# Core state machine
# ---------------------------------------------------------------------------

class SetupWizard:
    """Interactive setup wizard state machine."""

    def __init__(
        self,
        config_path: str,
        docker_client: "docker.DockerClient",
        anthropic_client: "anthropic.AsyncAnthropic | None" = None,
        unraid_api_key: str | None = None,
    ) -> None:
        self._config_path = config_path
        self._docker_client = docker_client
        self._anthropic_client = anthropic_client
        self._unraid_api_key = unraid_api_key
        self._sessions: dict[int, WizardSession] = {}
        self._classifier = ContainerClassifier(anthropic_client=anthropic_client)

    # -- session helpers --------------------------------------------------

    def _get_or_create_session(self, user_id: int) -> WizardSession:
        if user_id not in self._sessions:
            self._sessions[user_id] = WizardSession()
        return self._sessions[user_id]

    def get_state(self, user_id: int) -> WizardState:
        session = self._sessions.get(user_id)
        return session.state if session else WizardState.IDLE

    def is_active(self, user_id: int) -> bool:
        state = self.get_state(user_id)
        return state not in (WizardState.IDLE, WizardState.COMPLETE)

    def get_session_data(self, user_id: int) -> WizardSession:
        return self._get_or_create_session(user_id)

    # -- state transitions ------------------------------------------------

    def start(self, user_id: int) -> None:
        session = self._get_or_create_session(user_id)
        if self._unraid_api_key:
            session.state = WizardState.AWAITING_HOST
        else:
            session.state = WizardState.REVIEW_CONTAINERS

    def set_host(self, user_id: int, host: str) -> None:
        session = self._get_or_create_session(user_id)
        session.unraid_host = host
        session.state = WizardState.CONNECTING

    def connection_result(
        self, user_id: int, success: bool, port: int, use_ssl: bool
    ) -> None:
        session = self._get_or_create_session(user_id)
        if success:
            session.unraid_port = port
            session.unraid_use_ssl = use_ssl
            session.state = WizardState.REVIEW_CONTAINERS
        else:
            session.state = WizardState.AWAITING_HOST

    def confirm(self, user_id: int) -> None:
        session = self._get_or_create_session(user_id)
        session.state = WizardState.COMPLETE

    def cancel(self, user_id: int) -> None:
        """Reset the wizard back to idle state."""
        if user_id in self._sessions:
            del self._sessions[user_id]

    # -- connection test --------------------------------------------------

    async def test_unraid_connection(
        self, host: str, api_key: str
    ) -> tuple[bool, int, bool]:
        """Try connecting to Unraid API via HTTPS:443 then HTTP:80.

        Returns (success, port, use_ssl).
        """
        attempts = [
            (443, True),
            (80, False),
        ]
        for port, use_ssl in attempts:
            scheme = "https" if use_ssl else "http"
            url = f"{scheme}://{host}:{port}/graphql"
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        "x-api-key": api_key,
                        "Content-Type": "application/json",
                        "apollo-require-preflight": "true",
                    }
                    async with session.post(
                        url,
                        headers=headers,
                        json={"query": "{ info { os { hostname } } }"},
                        timeout=aiohttp.ClientTimeout(total=10),
                        ssl=False if use_ssl else None,
                    ) as resp:
                        logger.info(
                            f"Unraid connection test {scheme}://{host}:{port} "
                            f"-> status {resp.status}"
                        )
                        if resp.status < 500:
                            return (True, port, use_ssl)
            except Exception as e:
                logger.info(f"Unraid connection test {scheme}://{host}:{port} failed: {e}")
                continue

        return (False, 0, False)

    # -- docker containers ------------------------------------------------

    def get_docker_containers(self) -> list[tuple[str, str, str]]:
        """List all Docker containers as (name, image, status)."""
        try:
            containers = self._docker_client.containers.list(all=True)
            results: list[tuple[str, str, str]] = []
            for c in containers:
                name = c.name
                image_tags = c.image.tags
                image = image_tags[0] if image_tags else str(c.image.id)[:20]
                status = c.status
                results.append((name, image, status))
            return results
        except Exception as e:
            logger.error(f"Failed to list Docker containers: {e}")
            return []

    # -- classification ---------------------------------------------------

    async def classify_containers(
        self, user_id: int
    ) -> list[ContainerClassification]:
        """Fetch and classify containers, storing results in session."""
        containers = await asyncio.to_thread(self.get_docker_containers)
        classifications = await self._classifier.classify_all(containers)
        session = self._get_or_create_session(user_id)
        session.classifications = classifications
        return classifications

    # -- config save ------------------------------------------------------

    def save_config(self, user_id: int, merge: bool = False) -> None:
        """Save wizard results to config.yaml."""
        session = self._get_or_create_session(user_id)
        classifications = session.classifications

        # Extract containers per category
        watched: list[str] = []
        protected: list[str] = []
        ignored: list[str] = []
        priority: list[str] = []
        killable: list[str] = []

        for c in classifications:
            if "watched" in c.categories:
                watched.append(c.name)
            if "protected" in c.categories:
                protected.append(c.name)
            if "ignored" in c.categories:
                ignored.append(c.name)
            if "priority" in c.categories:
                priority.append(c.name)
            if "killable" in c.categories:
                killable.append(c.name)

        writer = ConfigWriter(self._config_path)
        kwargs = dict(
            unraid_host=session.unraid_host,
            unraid_port=session.unraid_port,
            unraid_use_ssl=session.unraid_use_ssl,
            watched_containers=watched,
            protected_containers=protected,
            ignored_containers=ignored,
            priority_containers=priority,
            killable_containers=killable,
        )
        if merge:
            writer.merge(**kwargs)
        else:
            writer.write(**kwargs)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_classification_summary(
    classifications: list[ContainerClassification],
) -> str:
    """Format a grouped summary message with emoji."""
    if not classifications:
        return "No containers found."

    # Group containers by category, tracking AI-suggested ones
    groups: dict[str, list[str]] = {cat: [] for cat in VALID_CATEGORIES}
    ai_names: set[str] = set()
    uncategorised: list[str] = []

    for c in classifications:
        if c.ai_suggested:
            ai_names.add(c.name)
        if not c.categories:
            uncategorised.append(c.name)
        else:
            for cat in c.categories:
                if cat in groups:
                    groups[cat].append(c.name)

    has_ai = bool(ai_names)
    lines: list[str] = ["*Container Classifications*\n"]
    for cat in ["priority", "protected", "watched", "killable", "ignored"]:
        names = groups.get(cat, [])
        if not names:
            continue
        emoji = _CATEGORY_EMOJI.get(cat, "")
        label = _CATEGORY_LABELS.get(cat, cat)
        lines.append(f"{emoji} *{label}*")
        for name in sorted(names):
            suffix = " \\*" if name in ai_names else ""
            lines.append(f"  - {name}{suffix}")
        lines.append("")

    if uncategorised:
        lines.append("*Uncategorised*")
        for name in sorted(uncategorised):
            suffix = " \\*" if name in ai_names else ""
            lines.append(f"  - {name}{suffix}")
        lines.append("")

    if has_ai:
        lines.append("_\\* = AI-suggested classification_")

    return "\n".join(lines)


def build_summary_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard with Adjust buttons per category + Looks Good."""
    buttons: list[list[InlineKeyboardButton]] = []

    for cat in ["priority", "protected", "watched", "killable", "ignored"]:
        emoji = _CATEGORY_EMOJI.get(cat, "")
        label = cat.capitalize()
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} Adjust {label}",
                callback_data=f"setup:adjust:{cat}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="\u2705 Looks Good",
            callback_data="setup:confirm",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_adjust_keyboard(
    classifications: list[ContainerClassification],
    category: str,
) -> InlineKeyboardMarkup:
    """Build a toggle keyboard for a category.

    Each container shows whether it is currently in the category.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    for c in sorted(classifications, key=lambda x: x.name):
        in_cat = category in c.categories
        mark = "\u2705" if in_cat else "\u274c"
        buttons.append([
            InlineKeyboardButton(
                text=f"{mark} {c.name}",
                callback_data=f"setup:toggle:{category}:{c.name}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="\u2b05\ufe0f Done",
            callback_data="setup:adjust_done",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# Telegram handler factories
# ---------------------------------------------------------------------------

def create_start_handler(
    wizard: SetupWizard,
) -> Callable[[Message], Awaitable[None]]:
    """Handle /start and /setup commands -- begins the wizard."""

    async def handler(message: Message) -> None:
        if not message.from_user:
            return
        user_id = message.from_user.id

        wizard.start(user_id)
        state = wizard.get_state(user_id)

        welcome = (
            "Welcome to the Unraid Monitor Bot setup wizard!\n\n"
            "I'll help you configure monitoring for your Docker containers"
        )

        if state == WizardState.AWAITING_HOST:
            welcome += " and Unraid server.\n\n"
            welcome += (
                "First, let's connect to your Unraid server.\n"
                "Please enter your Unraid server IP or hostname "
                "(e.g. `192.168.0.190`):"
            )
            await message.answer(welcome, parse_mode="Markdown")
        else:
            welcome += ".\n\n"
            welcome += "Scanning Docker containers..."
            await message.answer(welcome)

            classifications = await wizard.classify_containers(user_id)
            summary = format_classification_summary(classifications)
            keyboard = build_summary_keyboard()
            await message.answer(summary, reply_markup=keyboard, parse_mode="Markdown")

    return handler


def create_cancel_handler(
    wizard: SetupWizard,
) -> Callable[[Message], Awaitable[None]]:
    """Handle /cancel command -- exits the wizard."""

    async def handler(message: Message) -> None:
        if not message.from_user:
            return
        user_id = message.from_user.id

        if not wizard.is_active(user_id):
            await message.answer("No setup wizard is active.")
            return

        wizard.cancel(user_id)
        await message.answer(
            "Setup wizard cancelled. Use /setup to start again."
        )

    return handler


def create_host_handler(
    wizard: SetupWizard,
) -> Callable[[Message], Awaitable[None]]:
    """Handle host text input during AWAITING_HOST state."""

    async def handler(message: Message) -> None:
        if not message.from_user or not message.text:
            return
        user_id = message.from_user.id
        host = message.text.strip()

        wizard.set_host(user_id, host)

        await message.answer(f"Testing connection to `{host}`...", parse_mode="Markdown")

        api_key = wizard._unraid_api_key or ""
        success, port, use_ssl = await wizard.test_unraid_connection(host, api_key)

        wizard.connection_result(user_id, success, port, use_ssl)

        if success:
            scheme = "HTTPS" if use_ssl else "HTTP"
            await message.answer(
                f"Connected to Unraid via {scheme} on port {port}.\n\n"
                "Now scanning Docker containers..."
            )
            classifications = await wizard.classify_containers(user_id)
            summary = format_classification_summary(classifications)
            keyboard = build_summary_keyboard()
            await message.answer(summary, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await message.answer(
                f"Could not connect to `{host}` on port 443 or 80.\n\n"
                "Please check the IP/hostname and try again:",
                parse_mode="Markdown",
            )

    return handler


def create_confirm_callback(
    wizard: SetupWizard,
    on_complete: Callable[[], Awaitable[None]] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Handle 'Looks Good' button -- saves config and completes wizard."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.from_user:
            return
        user_id = callback.from_user.id

        await callback.answer("Saving configuration...")

        merge = os.path.exists(wizard._config_path)
        wizard.save_config(user_id, merge=merge)
        wizard.confirm(user_id)

        mode_label = "merged with existing" if merge else "saved"
        if callback.message:
            await callback.message.answer(
                f"Setup complete! Your configuration has been {mode_label}.\n\n"
                "The bot is now monitoring your containers. "
                "Use /help to see available commands."
            )

        if on_complete:
            await on_complete()

    return handler


def create_toggle_callback(
    wizard: SetupWizard,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Handle container toggle buttons within an Adjust view."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data:
            return
        user_id = callback.from_user.id

        # Parse callback_data: setup:toggle:<category>:<container_name>
        parts = callback.data.split(":", 3)
        if len(parts) < 4:
            await callback.answer("Invalid toggle data")
            return

        category = parts[2]
        container_name = parts[3]

        session = wizard.get_session_data(user_id)
        target = None
        for c in session.classifications:
            if c.name == container_name:
                target = c
                break

        if target is None:
            await callback.answer("Container not found")
            return

        # Toggle the category
        if category in target.categories:
            target.categories.discard(category)
            await callback.answer(f"Removed {container_name} from {category}")
        else:
            # Handle conflicts: ignored <-> watched are mutually exclusive
            if category == "ignored" and "watched" in target.categories:
                target.categories.discard("watched")
            elif category == "watched" and "ignored" in target.categories:
                target.categories.discard("ignored")
            target.categories.add(category)
            await callback.answer(f"Added {container_name} to {category}")

        # Refresh the keyboard
        keyboard = build_adjust_keyboard(session.classifications, category)
        emoji = _CATEGORY_EMOJI.get(category, "")
        label = _CATEGORY_LABELS.get(category, category)

        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                # Message might not be editable
                pass

    return handler


def create_adjust_callback(
    wizard: SetupWizard,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Handle 'Adjust <category>' buttons."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data:
            return
        user_id = callback.from_user.id

        # Parse callback_data: setup:adjust:<category>
        parts = callback.data.split(":", 2)
        if len(parts) < 3:
            await callback.answer("Invalid adjust data")
            return

        category = parts[2]
        session = wizard.get_session_data(user_id)
        session.adjusting_category = category
        session.state = WizardState.ADJUSTING

        emoji = _CATEGORY_EMOJI.get(category, "")
        label = _CATEGORY_LABELS.get(category, category)
        keyboard = build_adjust_keyboard(session.classifications, category)

        await callback.answer()
        if callback.message:
            await callback.message.answer(
                f"{emoji} *{label}*\n\n"
                "Tap a container to toggle it in/out of this category:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

    return handler


def create_adjust_done_callback(
    wizard: SetupWizard,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Handle 'Done' button -- returns to summary view."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.from_user:
            return
        user_id = callback.from_user.id

        session = wizard.get_session_data(user_id)
        session.state = WizardState.REVIEW_CONTAINERS
        session.adjusting_category = None

        summary = format_classification_summary(session.classifications)
        keyboard = build_summary_keyboard()

        await callback.answer()
        if callback.message:
            await callback.message.answer(
                summary,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

    return handler


# ---------------------------------------------------------------------------
# Setup mode middleware
# ---------------------------------------------------------------------------

class SetupModeMiddleware(BaseMiddleware):
    """Block non-wizard commands while setup is active.

    Allows /help and /setup through. Callback queries always pass through
    (for wizard buttons).
    """

    _ALLOWED_COMMANDS = {"/help", "/setup", "/cancel"}

    def __init__(self, wizard: SetupWizard) -> None:
        self._wizard = wizard
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        # Always let callback queries through (inline buttons)
        if isinstance(event, CallbackQuery):
            return await handler(event, data)

        # Only intercept Message events
        if not isinstance(event, Message):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else 0

        if not self._wizard.is_active(user_id):
            return await handler(event, data)

        # Allow whitelisted commands through
        text = (event.text or "").strip()
        for cmd in self._ALLOWED_COMMANDS:
            if text == cmd or text.startswith(cmd + " "):
                return await handler(event, data)

        # During wizard, let non-command text through (for host input etc.)
        if not text.startswith("/"):
            return await handler(event, data)

        # Block other commands during setup
        await event.answer(
            "Setup is in progress. Please complete the wizard first.\n"
            "Use /help if you need assistance."
        )
        return None
