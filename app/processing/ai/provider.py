"""
AI provider abstraction.

AIProvider is the swappable boundary between the invoice extraction service
and a concrete LLM vendor, mirroring the existing StorageService/OCREngine
pattern (Protocol + one concrete implementation) already used elsewhere in
this project. The extraction service (invoice_extraction_service.py) only
ever depends on this Protocol, never on a specific vendor SDK.

The provider's job stops at "call the LLM, get structured JSON back, handle
provider-level failures." Parsing that JSON into the validated
ExtractedInvoice schema is the extraction service's job, not the provider's
— this keeps schema/type parsing in one place regardless of which provider
is configured.
"""

from typing import Protocol

from app.core.exceptions import AIProviderError


class AIProviderTimeoutError(AIProviderError):
    """The provider did not respond within the configured timeout."""

    message = "AI extraction timed out."


class AIProviderAuthError(AIProviderError):
    """Missing/invalid API key or other provider authentication failure."""

    message = "AI provider is not configured correctly."


class AIProviderResponseError(AIProviderError):
    """The provider responded, but the response was empty or did not
    contain the expected structured tool-use output."""

    message = "AI provider returned an unusable response."


class AIProvider(Protocol):
    def extract_invoice_fields(self, document_text: str) -> dict:
        """
        Sends document_text to the LLM and returns the raw structured
        result as a plain dict (shaped like ExtractedInvoice, but NOT yet
        validated — that happens in invoice_extraction_service.py).

        Raises AIProviderTimeoutError, AIProviderAuthError,
        AIProviderResponseError, or AIProviderError itself for any other
        provider-level failure. Never raises the underlying SDK's raw
        exception type.
        """
        ...
