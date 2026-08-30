"""
Anthropic implementation of the AIProvider Protocol.

Uses tool-use (forcing a single specific tool call) for structured output,
rather than parsing free-form text — the tool's input_schema is derived
directly from ExtractedInvoice's Pydantic schema, so the schema is defined
in exactly one place (app/schemas/invoice_extraction.py) and can't drift out
of sync with what the extraction service later validates against.
"""

from typing import Any

import anthropic

from app.processing.ai.prompts import INVOICE_EXTRACTION_SYSTEM_PROMPT, build_user_message
from app.processing.ai.provider import (
    AIProviderAuthError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)
from app.schemas.invoice_extraction import ExtractedInvoice

_TOOL_NAME = "record_extracted_invoice"


def _build_tool_schema() -> dict:
    return {
        "name": _TOOL_NAME,
        "description": (
            "Records the structured invoice fields extracted from the document text. "
            "Fields not present in the text must be null."
        ),
        "input_schema": ExtractedInvoice.model_json_schema(),
    }


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_tokens: int = 4096,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        # Client construction is cheap and does not make a network call —
        # no lazy-loading concern here (unlike Phase 6's PaddleOCR model
        # weights), but the API key itself is only read from configuration,
        # never hard-coded.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)

    def extract_invoice_fields(self, document_text: str) -> dict:
        if not self._client.api_key:
            raise AIProviderAuthError("AI_API_KEY is not configured.")

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=INVOICE_EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_message(document_text)}],
                tools=[_build_tool_schema()],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            )
        except anthropic.AuthenticationError as exc:
            raise AIProviderAuthError("AI provider rejected the configured API key.") from exc
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise AIProviderTimeoutError("AI provider did not respond in time.") from exc
        except anthropic.APIError as exc:
            raise AIProviderResponseError("AI provider returned an error.") from exc

        return _extract_tool_input(response)


def _extract_tool_input(response: Any) -> dict:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
            return block.input
    raise AIProviderResponseError("AI provider response did not include the expected tool call.")
