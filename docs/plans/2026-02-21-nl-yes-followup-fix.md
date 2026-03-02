# NL "yes" Follow-Up Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make "yes" route to the NL processor when there's no pending command confirmation or diagnostic context, so conversational follow-ups work naturally.

**Architecture:** Make `YesFilter` and `DetailsFilter` context-aware by injecting `ConfirmationManager` and `DiagnosticService` respectively. When neither has pending state for the user, "yes" falls through to `NLFilter` and the NL processor handles it with conversation history. Also update the NL system prompt to encourage tool use over textual suggestions.

**Tech Stack:** Python 3.11, aiogram 3.x, pytest-asyncio

---

### Task 1: Make YesFilter context-aware

**Files:**
- Modify: `src/bot/telegram_bot.py` — `YesFilter` class (lines 72-78)
- Test: `tests/test_yes_handler.py`

**Step 1: Write the failing tests**

Add to `tests/test_yes_handler.py`:

```python
@pytest.mark.asyncio
async def test_yes_filter_rejects_when_no_pending_confirmation():
    """YesFilter should NOT match 'yes' when there's no pending confirmation."""
    from src.bot.telegram_bot import YesFilter
    from src.bot.confirmation import ConfirmationManager
    from aiogram.types import Message

    confirmation = ConfirmationManager()
    filter_instance = YesFilter(confirmation)

    message = MagicMock(spec=Message)
    message.text = "yes"
    message.from_user = MagicMock()
    message.from_user.id = 123

    result = await filter_instance(message)
    assert result is False, "Should not match when no pending confirmation"


@pytest.mark.asyncio
async def test_yes_filter_matches_when_pending_confirmation():
    """YesFilter should match 'yes' when there IS a pending confirmation."""
    from src.bot.telegram_bot import YesFilter
    from src.bot.confirmation import ConfirmationManager
    from aiogram.types import Message

    confirmation = ConfirmationManager()
    confirmation.request(user_id=123, action="restart", container_name="plex")
    filter_instance = YesFilter(confirmation)

    message = MagicMock(spec=Message)
    message.text = "yes"
    message.from_user = MagicMock()
    message.from_user.id = 123

    result = await filter_instance(message)
    assert result is True, "Should match when there's a pending confirmation"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_yes_handler.py -v`
Expected: FAIL — `YesFilter()` doesn't accept `confirmation` arg

**Step 3: Update YesFilter to accept ConfirmationManager**

In `src/bot/telegram_bot.py`, replace the `YesFilter` class:

```python
class YesFilter(Filter):
    """Filter for 'yes' confirmation messages.

    Only matches when the user has a pending command confirmation
    (from /restart, /stop, /start, /pull). When no confirmation is
    pending, 'yes' falls through to subsequent handlers (e.g. NL).
    """

    def __init__(self, confirmation: ConfirmationManager | None = None):
        self._confirmation = confirmation

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        if message.text.strip().lower() != "yes":
            return False
        # If no confirmation manager, match unconditionally (backward compat)
        if self._confirmation is None:
            return True
        # Only match if this user has a pending confirmation
        user_id = message.from_user.id if message.from_user else 0
        return self._confirmation.get_pending(user_id) is not None
```

**Step 4: Update the registration call to pass confirmation**

In `register_commands()`, find the YesFilter registration (around line 248) and change:

```python
# Before:
dp.message.register(
    create_confirm_handler(controller, confirmation),
    YesFilter(),
)

# After:
dp.message.register(
    create_confirm_handler(controller, confirmation),
    YesFilter(confirmation),
)
```

**Step 5: Update existing tests that construct YesFilter without args**

The existing test `test_yes_filter_matches_yes_variants` in `tests/test_yes_handler.py` constructs `YesFilter()` without args. This still works due to `confirmation=None` default (backward compat). No changes needed.

**Step 6: Run all tests to verify**

Run: `pytest tests/test_yes_handler.py tests/test_telegram_bot.py -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/bot/telegram_bot.py tests/test_yes_handler.py
git commit -m "fix: make YesFilter context-aware to allow NL follow-ups"
```

---

### Task 2: Make DetailsFilter context-aware

**Files:**
- Modify: `src/bot/telegram_bot.py` — `DetailsFilter` class (lines 81-89)
- Test: `tests/test_details_handler.py`

**Step 1: Write the failing tests**

Add to `tests/test_details_handler.py`:

```python
@pytest.mark.asyncio
async def test_details_filter_rejects_when_no_pending_diagnostic():
    """DetailsFilter should NOT match when there's no pending diagnostic."""
    from src.bot.telegram_bot import DetailsFilter
    from src.services.diagnostic import DiagnosticService

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.has_pending.return_value = False

    filter_instance = DetailsFilter(mock_service)

    message = MagicMock(spec=Message)
    message.text = "yes"
    message.from_user = MagicMock()
    message.from_user.id = 123

    result = await filter_instance(message)
    assert result is False, "Should not match when no pending diagnostic"


@pytest.mark.asyncio
async def test_details_filter_matches_when_pending_diagnostic():
    """DetailsFilter should match when there IS a pending diagnostic."""
    from src.bot.telegram_bot import DetailsFilter
    from src.services.diagnostic import DiagnosticService

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.has_pending.return_value = True

    filter_instance = DetailsFilter(mock_service)

    message = MagicMock(spec=Message)
    message.text = "more details"
    message.from_user = MagicMock()
    message.from_user.id = 123

    result = await filter_instance(message)
    assert result is True, "Should match when there's a pending diagnostic"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_details_handler.py -v`
Expected: FAIL — `DetailsFilter()` doesn't accept `diagnostic_service` arg

**Step 3: Update DetailsFilter to accept DiagnosticService**

In `src/bot/telegram_bot.py`, replace the `DetailsFilter` class:

```python
class DetailsFilter(Filter):
    """Filter for 'yes', 'more', 'details' follow-up messages.

    Only matches when the user has a pending diagnostic context
    (from /diagnose). When no context is pending, these words
    fall through to subsequent handlers (e.g. NL).
    """

    TRIGGERS = {"yes", "more", "details", "more details", "tell me more", "expand"}

    def __init__(self, diagnostic_service: Any | None = None):
        self._diagnostic_service = diagnostic_service

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        if message.text.strip().lower() not in self.TRIGGERS:
            return False
        # If no diagnostic service, match unconditionally (backward compat)
        if self._diagnostic_service is None:
            return True
        # Only match if this user has a pending diagnostic context
        user_id = message.from_user.id if message.from_user else 0
        return self._diagnostic_service.has_pending(user_id)
```

**Step 4: Update the registration call to pass diagnostic_service**

In `register_commands()`, find the DetailsFilter registration (around line 274) and change:

```python
# Before:
dp.message.register(
    create_details_handler(diagnostic_service),
    DetailsFilter(),
)

# After:
dp.message.register(
    create_details_handler(diagnostic_service),
    DetailsFilter(diagnostic_service),
)
```

**Step 5: Update existing tests**

The existing test `test_details_filter_matches_yes` in `tests/test_details_handler.py` constructs `DetailsFilter()` without args. This still works due to `diagnostic_service=None` default. No changes needed.

**Step 6: Run all tests to verify**

Run: `pytest tests/test_details_handler.py tests/test_telegram_bot.py -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/bot/telegram_bot.py tests/test_details_handler.py
git commit -m "fix: make DetailsFilter context-aware to allow NL follow-ups"
```

---

### Task 3: Update NL system prompt to prefer tool use

**Files:**
- Modify: `src/services/nl_processor.py` — `SYSTEM_PROMPT` constant (line 103)
- Test: `tests/test_nl_processor.py`

**Step 1: Write a test that the system prompt contains the new instruction**

Add to `tests/test_nl_processor.py`:

```python
def test_system_prompt_instructs_tool_use_for_actions():
    """System prompt should tell Claude to use tools rather than suggest actions textually."""
    from src.services.nl_processor import SYSTEM_PROMPT
    assert "use the appropriate tool" in SYSTEM_PROMPT.lower() or "call the" in SYSTEM_PROMPT.lower()
```

**Step 2: Run to verify it fails**

Run: `pytest tests/test_nl_processor.py::test_system_prompt_instructs_tool_use_for_actions -v`
Expected: FAIL

**Step 3: Update the system prompt**

In `src/services/nl_processor.py`, update the `SYSTEM_PROMPT`:

```python
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
- When the user wants an action (start, stop, restart, pull), call the appropriate tool immediately. Do NOT just suggest the action in text — the tool triggers the confirmation buttons the user needs.
- If a container is protected, explain you can't control it.
- If you can't help, suggest relevant /commands.

## Container name matching
Partial names work: "plex", "rad" for "radarr", etc."""
```

The key change is replacing "Suggest actions when appropriate, but explain why." with the instruction to call tools directly.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_nl_processor.py::test_system_prompt_instructs_tool_use_for_actions -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/services/nl_processor.py tests/test_nl_processor.py
git commit -m "fix: update NL prompt to use tools for actions instead of textual suggestions"
```

---

### Task 4: Integration smoke test

**Files:**
- Test: `tests/test_nl_integration.py`

**Step 1: Write integration test for the "yes" routing**

Add to `tests/test_nl_integration.py`:

```python
@pytest.mark.asyncio
async def test_yes_falls_through_to_nl_when_no_pending_state():
    """When no confirmation or diagnostic is pending, 'yes' should not be consumed by YesFilter or DetailsFilter."""
    from src.bot.telegram_bot import YesFilter, DetailsFilter
    from src.bot.confirmation import ConfirmationManager

    # Set up filters with no pending state
    confirmation = ConfirmationManager()
    yes_filter = YesFilter(confirmation)

    mock_diagnostic = MagicMock()
    mock_diagnostic.has_pending.return_value = False
    details_filter = DetailsFilter(mock_diagnostic)

    # Create a "yes" message
    message = MagicMock()
    message.text = "yes"
    message.from_user = MagicMock()
    message.from_user.id = 123

    # Neither filter should match
    assert await yes_filter(message) is False
    assert await details_filter(message) is False

    # NLFilter should still match it
    from src.bot.nl_handler import NLFilter
    nl_filter = NLFilter()
    assert await nl_filter(message) is True
```

**Step 2: Run the integration test**

Run: `pytest tests/test_nl_integration.py::test_yes_falls_through_to_nl_when_no_pending_state -v`
Expected: PASS (since we already implemented the filter changes in Tasks 1-2)

**Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add tests/test_nl_integration.py
git commit -m "test: add integration test for yes routing through to NL handler"
```
