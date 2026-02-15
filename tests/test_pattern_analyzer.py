"""Tests for Haiku-based pattern analysis."""

import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def mock_anthropic_client():
    client = MagicMock()
    return client


class TestPatternAnalyzer:
    @pytest.mark.asyncio
    async def test_analyze_returns_pattern(self, mock_anthropic_client):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        # Mock Haiku response
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '''```json
{
    "pattern": "Connection refused to .* on port \\\\d+",
    "match_type": "regex",
    "explanation": "Connection refused errors to any host on any port"
}
```'''
        mock_anthropic_client.messages.create = AsyncMock(return_value=mock_response)

        analyzer = PatternAnalyzer(mock_anthropic_client)

        result = await analyzer.analyze_error(
            container="sonarr",
            error_message="Connection refused to api.example.com on port 443",
            recent_logs=["log line 1", "log line 2"],
        )

        assert result is not None
        assert result["pattern"] == "Connection refused to .* on port \\d+"
        assert result["match_type"] == "regex"
        assert "Connection refused" in result["explanation"]

    @pytest.mark.asyncio
    async def test_analyze_returns_substring_for_static_errors(self, mock_anthropic_client):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '''```json
{
    "pattern": "Database connection pool exhausted",
    "match_type": "substring",
    "explanation": "Database pool exhaustion errors"
}
```'''
        mock_anthropic_client.messages.create = AsyncMock(return_value=mock_response)

        analyzer = PatternAnalyzer(mock_anthropic_client)

        result = await analyzer.analyze_error(
            container="app",
            error_message="Database connection pool exhausted",
            recent_logs=[],
        )

        assert result["match_type"] == "substring"

    @pytest.mark.asyncio
    async def test_analyze_returns_none_when_no_client(self):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        analyzer = PatternAnalyzer(None)

        result = await analyzer.analyze_error(
            container="sonarr",
            error_message="Some error",
            recent_logs=[],
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_error_caches_results(self):
        """Same error should return cached result without second API call."""
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"pattern": "error.*timeout", "match_type": "regex", "explanation": "Timeout errors"}')]
        mock_client.messages.create.return_value = mock_response

        analyzer = PatternAnalyzer(anthropic_client=mock_client)

        result1 = await analyzer.analyze_error("plex", "Connection timeout error", [])
        result2 = await analyzer.analyze_error("plex", "Connection timeout error", [])

        assert result1 == result2
        assert mock_client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_analyze_error_cache_expires(self):
        """Expired cache entries should trigger a new API call."""
        import time
        from unittest.mock import patch
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"pattern": "error.*timeout", "match_type": "regex", "explanation": "Timeout errors"}')]
        mock_client.messages.create.return_value = mock_response

        analyzer = PatternAnalyzer(anthropic_client=mock_client)

        result1 = await analyzer.analyze_error("plex", "Connection timeout error", [])

        # Expire the cache by manipulating the stored timestamp
        for key in analyzer._cache:
            ts, val = analyzer._cache[key]
            analyzer._cache[key] = (ts - PatternAnalyzer._CACHE_TTL - 1, val)

        result2 = await analyzer.analyze_error("plex", "Connection timeout error", [])

        assert result1 == result2
        assert mock_client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_analyze_error_failed_result_not_cached(self):
        """Failed analyses (returning None) should not be cached."""
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_client = AsyncMock()
        # First call: invalid JSON response
        bad_response = MagicMock()
        bad_response.content = [MagicMock(text='not valid json')]
        # Second call: valid response
        good_response = MagicMock()
        good_response.content = [MagicMock(text='{"pattern": "err", "match_type": "substring", "explanation": "Errors"}')]
        mock_client.messages.create.side_effect = [bad_response, good_response]

        analyzer = PatternAnalyzer(anthropic_client=mock_client)

        result1 = await analyzer.analyze_error("plex", "Some error", [])
        assert result1 is None

        result2 = await analyzer.analyze_error("plex", "Some error", [])
        assert result2 is not None
        assert result2["pattern"] == "err"
        assert mock_client.messages.create.call_count == 2
