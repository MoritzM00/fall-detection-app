from dataclasses import dataclass
from typing import Any

import httpx


class InferenceServiceError(RuntimeError):
    """Raised when the serving endpoint fails or violates its contract."""


@dataclass(frozen=True, slots=True)
class InferenceResponse:
    """Validated portion of a serving response needed by the pipeline."""

    content: str
    completion_id: str


class InferenceClient:
    """Small client for the vLLM-compatible contract used by the worker."""

    def __init__(self, base_url: str, timeout_seconds: float = 30) -> None:
        """Configure the serving endpoint and bounded request timeout."""
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def complete(self, payload: dict[str, Any]) -> InferenceResponse:
        """Send one non-streaming multimodal completion request."""
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            completion_id = body["id"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise InferenceServiceError(f"inference request failed: {exc}") from exc
        if not isinstance(content, str) or not isinstance(completion_id, str):
            raise InferenceServiceError("inference response contains invalid field types")
        return InferenceResponse(content=content, completion_id=completion_id)
