# Multi-Provider LLM Support Design

**Date:** 2026-02-21
**Status:** Approved

## Overview

Add support for multiple LLM providers (Anthropic, OpenAI, Ollama) with a provider abstraction layer, runtime model switching via `/model` command, and graceful degradation for providers that lack tool-calling support.

## Requirements

- **Providers:** Anthropic (existing), OpenAI, Ollama (OpenAI-compatible local models)
- **Feature parity:** Graceful degradation — try all features, fall back with a warning if unsupported
- **Model scope:** Global default + per-feature override in config.yaml
- **UX:** `/model` command with two-tap inline keyboard (pick provider, then pick model)

## Architecture

### Provider Protocol & Data Types

New module: `src/services/llm/provider.py`

```python
@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]

@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] | None
    stop_reason: str  # "end", "tool_use", "max_tokens"

@dataclass
class ModelInfo:
    id: str
    name: str        # Display name
    provider: str    # "anthropic", "openai", "ollama"
    supports_tools: bool

class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
    ) -> LLMResponse: ...

    @property
    def supports_tools(self) -> bool: ...

    @property
    def model_name(self) -> str: ...

    @property
    def provider_name(self) -> str: ...
```

All 4 AI consumers (NLProcessor, DiagnosticService, PatternAnalyzer, ContainerClassifier) use `LLMProvider` instead of the raw Anthropic client. They construct normalized messages and get normalized responses.

### Provider Implementations

**`AnthropicProvider`** — Wraps `anthropic.AsyncAnthropic`:
- Translates normalized tool definitions to Anthropic format
- Applies `cache_control` on system prompts and tool definitions (prompt caching)
- Maps `stop_reason` values and `content` blocks to `LLMResponse`

**`OpenAIProvider`** — Wraps `openai.AsyncOpenAI`:
- Translates tool definitions to OpenAI function-calling format
- Converts system prompts to `{"role": "system"}` messages
- Maps `finish_reason` and `tool_calls` to `LLMResponse`

**`OllamaProvider`** — Extends `OpenAIProvider`:
- Uses `base_url="http://host:11434/v1"` with OpenAI SDK
- Adds model discovery via Ollama's `GET /api/tags` endpoint
- Detects tool-calling support per model (sets `supports_tools` accordingly)

### Provider Registry

New file: `src/services/llm/registry.py`

```python
class ProviderRegistry:
    def __init__(self, config: AppConfig, settings: Settings):
        # Initialize providers based on available API keys

    def get_provider(self, feature: str = "default") -> LLMProvider | None
    def set_model(self, provider_name: str, model_name: str) -> None
    def get_available_providers(self) -> list[ProviderInfo]
    def get_available_models(self, provider: str) -> list[ModelInfo]
```

Single object passed to all AI consumers. Resolves which provider/model to use based on global default or per-feature override.

### Configuration

**New environment variables:**
```
OPENAI_API_KEY=         # Optional - enables OpenAI models
OLLAMA_HOST=            # Optional - defaults to http://localhost:11434
```

**Extended config.yaml `ai` section:**
```yaml
ai:
  default_provider: anthropic
  default_model: claude-haiku-4-5-20251001

  # Per-feature overrides (optional)
  models:
    nl_processor: claude-sonnet-4-5-20250929
    diagnostic: claude-haiku-4-5-20251001
    pattern_analyzer: claude-haiku-4-5-20251001

  # Provider-specific settings
  providers:
    anthropic:
      prompt_caching: true
    openai:
      organization: null
    ollama:
      host: http://localhost:11434

  # Token limits (unchanged)
  max_tokens:
    pattern_analyzer: 500
    nl_processor: 1024
    diagnostic_brief: 300
    diagnostic_detail: 800
```

Model names are provider-agnostic — the registry auto-detects provider from model name (e.g. `gpt-4o` -> OpenAI, `claude-*` -> Anthropic, Ollama tags -> Ollama).

### /model Command

New file: `src/bot/model_command.py`

Flow:
1. `/model` → shows current model + provider buttons (only enabled providers)
2. Tap provider → shows that provider's model buttons
3. Tap model → switches, confirms: "Switched to gpt-4o (OpenAI)"

Callback data: `model:provider_name` (step 2), `model_select:provider:model` (step 3).

Selection persisted to `data/model_selection.json` (runtime preference, not config.yaml).

### Consumer Migration

Each AI consumer changes constructor from `anthropic_client: AsyncAnthropic` to `provider: LLMProvider | None`.

```python
# Before
response = await self._anthropic.messages.create(model=..., max_tokens=..., messages=[...])
text = response.content[0].text

# After
response = await self._provider.chat(messages=[...], max_tokens=...)
text = response.text
```

NLProcessor tool-use loop: check `response.stop_reason == "tool_use"`, iterate `response.tool_calls` instead of `response.content` blocks.

### Graceful Degradation

When `provider.supports_tools == False`:
- NLProcessor falls back to text-only mode (no tool loop)
- Response includes note: "(Tool actions unavailable with this model — use /commands for container control)"

### File Layout

```
src/services/llm/
├── __init__.py              # Re-exports LLMProvider, ProviderRegistry
├── provider.py              # Protocol, LLMResponse, ToolCall, ModelInfo
├── registry.py              # ProviderRegistry
├── anthropic_provider.py
├── openai_provider.py
└── ollama_provider.py

src/bot/model_command.py     # /model command handler
data/model_selection.json    # Runtime model preference (auto-created)
```

### Testing Strategy

- Unit test each provider with mocked SDK clients
- Integration test the registry with multiple providers
- Test NLProcessor tool-use loop with both tool-supporting and non-tool providers
- Test /model command callbacks
- Existing tests continue to pass (they mock the Anthropic client, which becomes the provider)
