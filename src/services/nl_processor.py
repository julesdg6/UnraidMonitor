# src/services/nl_processor.py
import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.services.nl_tools import get_tool_definitions
from src.utils.api_errors import handle_anthropic_error
from src.utils.rate_limiter import PerUserRateLimiter

logger = logging.getLogger(__name__)

@dataclass
class ConversationMemory:
    """Stores conversation history for a single user."""

    user_id: int
    max_exchanges: int = 5
    messages: deque = field(default_factory=deque)
    last_activity: datetime = field(default_factory=lambda: datetime.now())
    pending_action: dict[str, Any] | None = None

    def __post_init__(self):
        """Set maxlen on deque after dataclass init."""
        if not isinstance(self.messages, deque) or self.messages.maxlen is None:
            self.messages = deque(self.messages, maxlen=self.max_exchanges * 2)

    def add_exchange(self, user_message: str, assistant_message: str) -> None:
        """Add a user/assistant exchange. Oldest auto-evicted by deque maxlen."""
        self.messages.append({"role": "user", "content": user_message})
        self.messages.append({"role": "assistant", "content": assistant_message})
        self.last_activity = datetime.now()

    def get_messages(self) -> list[dict[str, str]]:
        """Return a copy of messages for use in API calls."""
        return list(self.messages)

    def clear(self) -> None:
        """Clear all messages and pending action."""
        self.messages.clear()
        self.pending_action = None


class MemoryStore:
    """Stores conversation memories for all users with TTL expiration."""

    def __init__(
        self,
        max_exchanges: int = 5,
        memory_ttl_minutes: int = 30,
        max_users: int = 100,
    ):
        self._memories: dict[int, ConversationMemory] = {}
        self._max_exchanges = max_exchanges
        self._memory_ttl = timedelta(minutes=memory_ttl_minutes)
        self._max_users = max_users

    def get_or_create(self, user_id: int) -> ConversationMemory:
        """Get existing memory or create new one for user."""
        self._cleanup_expired()

        if user_id not in self._memories:
            # Enforce max users limit by evicting oldest
            if len(self._memories) >= self._max_users:
                self._evict_oldest()
            self._memories[user_id] = ConversationMemory(
                user_id=user_id,
                max_exchanges=self._max_exchanges,
            )
        return self._memories[user_id]

    def get(self, user_id: int) -> ConversationMemory | None:
        """Get memory for user if it exists."""
        return self._memories.get(user_id)

    def clear_user(self, user_id: int) -> None:
        """Remove memory for a user."""
        self._memories.pop(user_id, None)

    def _cleanup_expired(self) -> None:
        """Remove memories that have exceeded TTL."""
        now = datetime.now()
        expired = [
            uid for uid, mem in self._memories.items()
            if now - mem.last_activity > self._memory_ttl
        ]
        for uid in expired:
            del self._memories[uid]

    def _evict_oldest(self) -> None:
        """Remove the oldest memory to make room for new ones."""
        if not self._memories:
            return
        oldest_uid = min(
            self._memories.keys(),
            key=lambda uid: self._memories[uid].last_activity
        )
        del self._memories[oldest_uid]


SYSTEM_PROMPT = """You are an assistant for monitoring an Unraid server. You help users understand what's happening with their Docker containers and server, and can take actions to fix problems.

## Your capabilities
- Check container status, logs, and resource usage
- View server stats (CPU, memory, temperatures)
- Check array and disk health
- Restart, stop, start, or pull containers (with user confirmation)

## Guidelines
- Be concise. Users are on mobile Telegram.
- When investigating issues, gather relevant data before responding.
- For "what's wrong" questions: check status, recent errors, and logs.
- For performance questions: check resource usage first.
- Suggest actions when appropriate, but explain why.
- If a container is protected, explain you can't control it.
- If you can't help, suggest relevant /commands.

## Container name matching
Partial names work: "plex", "rad" for "radarr", etc."""


@dataclass
class ProcessResult:
    """Result from processing a natural language message."""

    response: str
    pending_action: dict[str, Any] | None = None


class NLProcessor:
    """Processes natural language messages using Claude API with tools."""

    def __init__(
        self,
        anthropic_client: Any | None,
        tool_executor: Any,
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 1024,
        max_tool_iterations: int = 10,
        max_conversation_exchanges: int = 5,
        rate_limit_per_minute: int = 10,
        rate_limit_per_hour: int = 60,
    ):
        """Initialize the NLProcessor.

        Args:
            anthropic_client: Anthropic client for Claude API calls, or None if not configured.
            tool_executor: Executor for tool calls (NLToolExecutor instance).
            model: Claude model to use for processing.
            max_tokens: Maximum tokens for Claude API responses.
            max_tool_iterations: Maximum tool use loop iterations.
            max_conversation_exchanges: Maximum conversation exchanges to keep in memory.
            rate_limit_per_minute: Max NL requests per user per minute.
            rate_limit_per_hour: Max NL requests per user per hour.
        """
        self._anthropic = anthropic_client
        self._executor = tool_executor
        self._model = model
        self._max_tokens = max_tokens
        self._max_tool_iterations = max_tool_iterations
        self.memory_store = MemoryStore(max_exchanges=max_conversation_exchanges)
        self._rate_limiter = PerUserRateLimiter(
            max_per_minute=rate_limit_per_minute,
            max_per_hour=rate_limit_per_hour,
        )
        self._cached_tools: list[dict[str, Any]] | None = None
        self._user_locks: dict[int, asyncio.Lock] = {}

    async def process(self, user_id: int, message: str) -> ProcessResult:
        """Process a natural language message and return a response.

        Args:
            user_id: Telegram user ID for conversation tracking.
            message: The user's message to process.

        Returns:
            ProcessResult with response text and optional pending_action.
        """
        if self._anthropic is None:
            return ProcessResult(
                response="Sorry, natural language processing is not configured. Please use /commands instead."
            )

        # Check rate limit
        if not self._rate_limiter.is_allowed(user_id):
            retry_after = self._rate_limiter.get_retry_after(user_id)
            return ProcessResult(
                response=f"Rate limit reached. Please wait {retry_after} seconds before sending another message."
            )

        # Validate message length
        max_message_length = 2000
        if len(message) > max_message_length:
            return ProcessResult(
                response=f"Message too long ({len(message)} chars). Maximum is {max_message_length}."
            )

        # Per-user lock prevents concurrent requests from garbling conversation history
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()

        async with self._user_locks[user_id]:
            memory = self.memory_store.get_or_create(user_id)

            # Clear any pending action when new message arrives
            memory.pending_action = None

            # Build messages with history
            messages = memory.get_messages()
            messages.append({"role": "user", "content": message})

            try:
                response_text, pending_action = await self._call_claude(messages)

                # Store the exchange
                memory.add_exchange(message, response_text)

                # Store pending action if any
                if pending_action:
                    memory.pending_action = pending_action

                return ProcessResult(response=response_text, pending_action=pending_action)

            except Exception as e:
                error_result = handle_anthropic_error(e)
                logger.log(error_result.log_level, f"NL processing error: {e}")
                return ProcessResult(
                    response=f"Sorry, {error_result.user_message.lower()} Try using /commands instead."
                )

    def _get_cached_tools(self) -> list[dict[str, Any]]:
        """Return cached tool definitions (built once)."""
        if self._cached_tools is None:
            self._cached_tools = get_tool_definitions()
        return self._cached_tools

    async def _call_claude(self, messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
        """Call Claude API with tool support.

        Args:
            messages: List of message dicts for the conversation.

        Returns:
            Tuple of (response_text, pending_action).
        """
        assert self._anthropic is not None  # Caller ensures this via process() check
        tools = self._get_cached_tools()
        pending_action = None

        # Initial API call
        response = await self._anthropic.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        # Handle tool use loop with max iterations guard
        iterations = 0
        while response.stop_reason == "tool_use" and iterations < self._max_tool_iterations:
            iterations += 1
            # Extract tool calls
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input

                    # Execute the tool
                    result = await self._executor.execute(tool_name, tool_input)

                    # Check for confirmation needed
                    if result.startswith("CONFIRMATION_NEEDED:"):
                        _, action, container = result.split(":", 2)
                        pending_action = {"action": action, "container": container}
                        result = f"Confirmation needed to {action} {container}."

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            # Continue conversation with tool results
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results},
            ]

            response = await self._anthropic.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )

        if iterations >= self._max_tool_iterations:
            logger.warning("Max tool iterations reached")

        # Extract final text response
        text_parts = [block.text for block in response.content if block.type == "text"]
        response_text = "\n".join(text_parts) if text_parts else "I couldn't generate a response."

        return response_text, pending_action
