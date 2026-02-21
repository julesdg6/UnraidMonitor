"""Tests for LLM API error handling utilities."""

import logging
from unittest.mock import MagicMock

import pytest

from src.utils.api_errors import handle_anthropic_error, handle_llm_error, APIErrorResult


class TestHandleAnthropicError:
    """Tests for the handle_anthropic_error function."""

    def test_generic_exception_returns_error_result(self):
        """Generic exceptions return appropriate error result."""
        error = ValueError("Something went wrong")
        result = handle_anthropic_error(error)

        assert isinstance(result, APIErrorResult)
        assert "unexpected error" in result.user_message.lower()
        assert result.is_retryable is False
        assert result.log_level == logging.ERROR

    def test_runtime_error_shows_type_name(self):
        """Error type name is included in message for unknown errors."""
        error = RuntimeError("Test error")
        result = handle_anthropic_error(error)

        assert "RuntimeError" in result.user_message

    def test_rate_limit_error_handling(self):
        """Rate limit errors return retryable result with appropriate message."""
        try:
            import anthropic

            error = anthropic.RateLimitError(
                message="Rate limit exceeded",
                response=MagicMock(),
                body=None,
            )
            result = handle_anthropic_error(error)

            assert result.is_retryable is True
            assert "rate limit" in result.user_message.lower()
            assert result.log_level == logging.WARNING
        except ImportError:
            pytest.skip("anthropic module not available")

    def test_authentication_error_handling(self):
        """Authentication errors return non-retryable result."""
        try:
            import anthropic

            error = anthropic.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(),
                body=None,
            )
            result = handle_anthropic_error(error)

            assert result.is_retryable is False
            assert "authentication" in result.user_message.lower()
            assert result.log_level == logging.ERROR
        except ImportError:
            pytest.skip("anthropic module not available")

    def test_connection_error_handling(self):
        """Connection errors return retryable result."""
        try:
            import anthropic

            error = anthropic.APIConnectionError(
                message="Connection failed",
                request=MagicMock(),
            )
            result = handle_anthropic_error(error)

            assert result.is_retryable is True
            assert "connect" in result.user_message.lower()
            assert result.log_level == logging.WARNING
        except ImportError:
            pytest.skip("anthropic module not available")

    def test_bad_request_error_handling(self):
        """Bad request errors are not retryable."""
        try:
            import anthropic

            error = anthropic.BadRequestError(
                message="Invalid request",
                response=MagicMock(),
                body=None,
            )
            result = handle_anthropic_error(error)

            assert result.is_retryable is False
            assert "invalid request" in result.user_message.lower()
        except ImportError:
            pytest.skip("anthropic module not available")


class TestAPIErrorResult:
    """Tests for the APIErrorResult dataclass."""

    def test_default_log_level(self):
        """Default log level is ERROR."""
        result = APIErrorResult(
            user_message="Test",
            is_retryable=False,
        )
        assert result.log_level == logging.ERROR

    def test_custom_log_level(self):
        """Custom log level can be set."""
        result = APIErrorResult(
            user_message="Test",
            is_retryable=True,
            log_level=logging.WARNING,
        )
        assert result.log_level == logging.WARNING


class TestHandleLLMError:
    """Tests for handle_llm_error with OpenAI errors and backward compatibility."""

    def test_handle_openai_rate_limit(self):
        """OpenAI rate limit errors return retryable result with rate limit message."""
        try:
            import openai

            error = openai.RateLimitError(
                message="Rate limit exceeded",
                response=MagicMock(),
                body=None,
            )
            result = handle_llm_error(error)

            assert result.is_retryable is True
            assert "rate limit" in result.user_message.lower()
            assert result.log_level == logging.WARNING
        except ImportError:
            pytest.skip("openai module not available")

    def test_handle_openai_auth_error(self):
        """OpenAI authentication errors return non-retryable result."""
        try:
            import openai

            error = openai.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(),
                body=None,
            )
            result = handle_llm_error(error)

            assert result.is_retryable is False
            assert "authentication" in result.user_message.lower()
            assert result.log_level == logging.ERROR
        except ImportError:
            pytest.skip("openai module not available")

    def test_handle_openai_bad_request(self):
        """OpenAI bad request errors return non-retryable result."""
        try:
            import openai

            error = openai.BadRequestError(
                message="Invalid request",
                response=MagicMock(),
                body=None,
            )
            result = handle_llm_error(error)

            assert result.is_retryable is False
            assert result.log_level == logging.ERROR
        except ImportError:
            pytest.skip("openai module not available")

    def test_handle_openai_connection_error(self):
        """OpenAI connection errors return retryable result."""
        try:
            import openai

            error = openai.APIConnectionError(
                message="Connection failed",
                request=MagicMock(),
            )
            result = handle_llm_error(error)

            assert result.is_retryable is True
            assert "connect" in result.user_message.lower()
            assert result.log_level == logging.WARNING
        except ImportError:
            pytest.skip("openai module not available")

    def test_handle_llm_error_backward_compat(self):
        """handle_anthropic_error is a backward-compatible alias for handle_llm_error."""
        assert handle_anthropic_error is handle_llm_error

    def test_handle_llm_error_generic(self):
        """Generic exceptions are handled by handle_llm_error with type name in message."""
        error = ValueError("Something went wrong")
        result = handle_llm_error(error)

        assert isinstance(result, APIErrorResult)
        assert "unexpected error" in result.user_message.lower()
        assert result.is_retryable is False
        assert result.log_level == logging.ERROR
