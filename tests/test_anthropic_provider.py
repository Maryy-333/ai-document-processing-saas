from unittest.mock import MagicMock, patch

import anthropic
import pytest

from app.processing.ai.anthropic_provider import AnthropicProvider
from app.processing.ai.provider import (
    AIProvider,
    AIProviderAuthError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)


def _make_tool_use_response(input_dict: dict):
    block = MagicMock()
    block.type = "tool_use"
    block.name = "record_extracted_invoice"
    block.input = input_dict
    response = MagicMock()
    response.content = [block]
    return response


def test_anthropic_provider_satisfies_protocol():
    provider: AIProvider = AnthropicProvider(
        api_key="x", model="claude-haiku-4-5", timeout_seconds=30
    )
    assert hasattr(provider, "extract_invoice_fields")


def test_missing_api_key_raises_auth_error():
    provider = AnthropicProvider(api_key="", model="claude-haiku-4-5", timeout_seconds=30)
    with pytest.raises(AIProviderAuthError):
        provider.extract_invoice_fields("some invoice text")


def test_successful_tool_use_response_returns_dict():
    provider = AnthropicProvider(api_key="fake-key", model="claude-haiku-4-5", timeout_seconds=30)
    expected = {"vendor_name": "Acme", "total": "100.00"}

    with patch.object(
        provider._client.messages, "create", return_value=_make_tool_use_response(expected)
    ) as mock_create:
        result = provider.extract_invoice_fields("invoice text here")
        assert result == expected
        assert mock_create.call_count == 1
        # Confirm tool_choice forces the extraction tool, not free-form text.
        _, kwargs = mock_create.call_args
        assert kwargs["tool_choice"]["name"] == "record_extracted_invoice"


def test_response_without_tool_use_block_raises_response_error():
    provider = AnthropicProvider(api_key="fake-key", model="claude-haiku-4-5", timeout_seconds=30)
    text_block = MagicMock()
    text_block.type = "text"
    response = MagicMock()
    response.content = [text_block]

    with patch.object(provider._client.messages, "create", return_value=response):
        with pytest.raises(AIProviderResponseError):
            provider.extract_invoice_fields("invoice text here")


def test_authentication_error_translated():
    provider = AnthropicProvider(api_key="fake-key", model="claude-haiku-4-5", timeout_seconds=30)
    with patch.object(
        provider._client.messages,
        "create",
        side_effect=anthropic.AuthenticationError(
            "bad key", response=MagicMock(status_code=401), body=None
        ),
    ):
        with pytest.raises(AIProviderAuthError):
            provider.extract_invoice_fields("invoice text here")


def test_timeout_error_translated():
    provider = AnthropicProvider(api_key="fake-key", model="claude-haiku-4-5", timeout_seconds=30)
    with patch.object(
        provider._client.messages,
        "create",
        side_effect=anthropic.APITimeoutError(request=MagicMock()),
    ):
        with pytest.raises(AIProviderTimeoutError):
            provider.extract_invoice_fields("invoice text here")


def test_generic_api_error_translated():
    provider = AnthropicProvider(api_key="fake-key", model="claude-haiku-4-5", timeout_seconds=30)
    with patch.object(
        provider._client.messages,
        "create",
        side_effect=anthropic.APIError(
            "server error", request=MagicMock(), body=None
        ),
    ):
        with pytest.raises(AIProviderResponseError):
            provider.extract_invoice_fields("invoice text here")
