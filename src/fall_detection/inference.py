import hashlib
import json
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

    def __init__(
        self, base_url: str, timeout_seconds: float = 30, mock_fixture_identity: str | None = None
    ) -> None:
        """Configure the serving endpoint and bounded request timeout."""
        self._mock_fixture_identity = mock_fixture_identity
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def discover_mock_identity(self) -> str:
        """Read effective service identity on the mock-only discovery endpoint."""
        try:
            response = httpx.get(f"{self._base_url}/mock/identity", timeout=self._timeout_seconds)
            response.raise_for_status()
            body = response.json()
            identity = body["fixture_version"]
            digest = hashlib.sha256(
                json.dumps(
                    body["manifest"], sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            ).hexdigest()
            if (
                body["backend"] != "mock"
                or not isinstance(identity, str)
                or identity != f"mock-v1-sha256:{digest}"
            ):
                raise ValueError("Invalid mock service identity")
            return identity
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise InferenceServiceError(f"Mock fixture discovery failed: {exc}") from exc

    def complete(self, payload: dict[str, Any]) -> InferenceResponse:
        """Send one non-streaming multimodal completion request."""
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                timeout=self._timeout_seconds,
                headers=(
                    {"X-Mock-Fixture-Identity": self._mock_fixture_identity}
                    if self._mock_fixture_identity
                    else None
                ),
            )
            response.raise_for_status()
            if (
                self._mock_fixture_identity
                and response.headers.get("X-Mock-Fixture-Identity") != self._mock_fixture_identity
            ):
                raise ValueError("Required mock fixture identity is unavailable")
            body = response.json()
            choice = body["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError("Inference response contains an invalid choice")
            if choice.get("finish_reason") == "length":
                raise ValueError("Inference answer was truncated")
            content = body["choices"][0]["message"]["content"]
            completion_id = body["id"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise InferenceServiceError(f"inference request failed: {exc}") from exc
        if not isinstance(content, str) or not isinstance(completion_id, str):
            raise InferenceServiceError("inference response contains invalid field types")
        return InferenceResponse(content=content, completion_id=completion_id)
