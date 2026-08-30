"""
Fake AIProvider implementations for tests. Satisfy the AIProvider Protocol
without requiring a real API key or network access (Phase 7 §"Testing" -
tests must not depend on an external LLM API).
"""

from app.processing.ai.provider import (
    AIProviderAuthError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)


class FakeAIProvider:
    """Returns a fixed structured dict, regardless of input text."""

    def __init__(self, result: dict | None = None) -> None:
        self.result = result if result is not None else {}
        self.call_count = 0
        self.last_document_text: str | None = None

    def extract_invoice_fields(self, document_text: str) -> dict:
        self.call_count += 1
        self.last_document_text = document_text
        return self.result


class TimeoutAIProvider:
    def extract_invoice_fields(self, document_text: str) -> dict:
        raise AIProviderTimeoutError("Simulated timeout.")


class AuthFailureAIProvider:
    def extract_invoice_fields(self, document_text: str) -> dict:
        raise AIProviderAuthError("Simulated auth failure.")


class EmptyResponseAIProvider:
    def extract_invoice_fields(self, document_text: str) -> dict:
        raise AIProviderResponseError("Simulated empty/unusable response.")


class MalformedResponseAIProvider:
    """Returns a structurally-invalid response (wrong types) that will fail
    Pydantic validation in the extraction service."""

    def extract_invoice_fields(self, document_text: str) -> dict:
        return {
            "vendor_name": 12345,  # should be a string
            "subtotal": "not-a-number-at-all-$$$",
            "line_items": "this should be a list",
        }


class UnexpectedErrorAIProvider:
    """Simulates a provider adapter that let a raw, untranslated exception
    escape — the extraction service must still handle this safely."""

    def extract_invoice_fields(self, document_text: str) -> dict:
        raise RuntimeError("Some unexpected SDK-internal failure.")
