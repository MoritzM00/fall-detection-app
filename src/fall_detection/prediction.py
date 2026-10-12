"""One input and configuration to one validated activity prediction.

Covers the label taxonomy, the thesis prompt, deterministic frame sampling, the
vLLM-compatible HTTP transport, and answer parsing. This module must not depend on
web routes, database models, or the frontend; persistence belongs to the application.
"""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, cast

import httpx

type ActivityLabel = Literal[
    "walk",
    "fall",
    "fallen",
    "sit_down",
    "sitting",
    "lie_down",
    "lying",
    "stand_up",
    "standing",
    "other",
    "kneel_down",
    "kneeling",
    "squat_down",
    "squatting",
    "crawl",
    "jump",
]

ACTIVITY_LABELS: tuple[ActivityLabel, ...] = (
    "walk",
    "fall",
    "fallen",
    "sit_down",
    "sitting",
    "lie_down",
    "lying",
    "stand_up",
    "standing",
    "other",
    "kneel_down",
    "kneeling",
    "squat_down",
    "squatting",
    "crawl",
    "jump",
)


THESIS_BASELINE_PROMPT = """You are given a short video clip showing one primary human activity.
Classify the action in the first part of the clip using exactly one label from:
walk, fall, fallen, sit_down, sitting, lie_down, lying, stand_up, standing,
other, kneel_down, kneeling, squat_down, squatting, crawl, jump.

Account for the fact that adjacent clips may overlap. Respond only in this format:
The best answer is: <class_label>"""

PRESET_ID = "thesis-baseline-v1"


_ANSWER_PATTERN = re.compile(r"^\s*The best answer is:\s*([a-z_]+)\s*$")


class PredictionParseError(ValueError):
    """Raised when generated text is not one unambiguous canonical label."""


def parse_activity_label(raw_response: str, *, allow_bare_label: bool = False) -> ActivityLabel:
    """Parse a fixed-format answer or an explicitly allowed single canonical label."""
    match = _ANSWER_PATTERN.fullmatch(raw_response)
    if match is None:
        candidate = raw_response.strip()
        if allow_bare_label and candidate in ACTIVITY_LABELS:
            return candidate
        raise PredictionParseError("response does not match the required answer format")

    candidate = match.group(1)
    if candidate not in ACTIVITY_LABELS:
        raise PredictionParseError(f"unknown activity label: {candidate}")
    return cast("ActivityLabel", candidate)


def sample_timestamps(
    start_seconds: float, end_seconds: float, frame_count: int = 16
) -> list[float]:
    """Return deterministic timestamps spanning the selected range."""
    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds) or start_seconds < 0:
        raise ValueError("timestamps must be finite and non-negative")
    if end_seconds <= start_seconds:
        raise ValueError("end_seconds must be greater than start_seconds")
    if frame_count < 2:
        raise ValueError("frame_count must be at least 2")
    step = (end_seconds - start_seconds) / (frame_count - 1)
    return [round(start_seconds + index * step, 3) for index in range(frame_count)]


class InferenceServiceError(RuntimeError):
    """Raised when the serving endpoint fails or violates its contract."""


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def require_encrypted_transport(base_url: str) -> None:
    """Refuse to send frames over plaintext HTTP to anything but this machine.

    Loopback HTTP covers the local mock and SSH tunnels, whose traffic never leaves the host.
    """
    url = httpx.URL(base_url)
    if url.scheme == "https" or (url.scheme == "http" and url.host in LOOPBACK_HOSTS):
        return
    raise InferenceServiceError(
        f"Refusing to send video frames to {url.scheme}://{url.host}: use https, "
        "or http only to this machine (127.0.0.1, localhost)"
    )


@dataclass(frozen=True, slots=True)
class InferenceResponse:
    """Validated portion of a serving response needed by the pipeline."""

    content: str
    completion_id: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _token_count(usage: object, key: str) -> int | None:
    """Read one optional usage counter; a missing or malformed count is not an error."""
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class InferenceClient:
    """Small client for the vLLM-compatible contract used by the worker."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30,
        mock_fixture_identity: str | None = None,
        auth_headers: Mapping[str, str] | None = None,
    ) -> None:
        """Configure the serving endpoint, credentials and bounded request timeout."""
        self._mock_fixture_identity = mock_fixture_identity
        self._auth_headers = dict(auth_headers or {})
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
                # Modal answers requests running past ~150 s with a 303 to poll for the result.
                follow_redirects=True,
                headers={
                    **self._auth_headers,
                    **(
                        {"X-Mock-Fixture-Identity": self._mock_fixture_identity}
                        if self._mock_fixture_identity
                        else {}
                    ),
                },
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
            usage = body.get("usage")
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise InferenceServiceError(f"inference request failed: {exc}") from exc
        if not isinstance(content, str) or not isinstance(completion_id, str):
            raise InferenceServiceError("inference response contains invalid field types")
        return InferenceResponse(
            content=content,
            completion_id=completion_id,
            prompt_tokens=_token_count(usage, "prompt_tokens"),
            completion_tokens=_token_count(usage, "completion_tokens"),
        )


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Worker result before application persistence metadata is added."""

    label: ActivityLabel
    raw_response: str
    sampled_timestamps: list[float]
    request_duration_ms: float
    total_duration_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def build_inference_payload(
    model: str,
    video_data_url: str,
    prompt: str,
    video_metadata: dict[str, Any] | None = None,
    generation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the online video request shared by mock and vLLM serving."""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "video_url", "video_url": {"url": video_data_url}},
                ],
            }
        ],
        "temperature": (generation or {}).get("temperature", 0),
        "max_tokens": (generation or {}).get("max_tokens", 32),
        "stream": False,
    }
    if video_metadata is not None:
        payload["media_io_kwargs"] = {"video": video_metadata}
    return payload


def run_pipeline(
    *,
    client: InferenceClient,
    model: str,
    video_data_url: str,
    start_seconds: float,
    end_seconds: float,
    prompt: str = THESIS_BASELINE_PROMPT,
    sampled_timestamps: list[float] | None = None,
    video_metadata: dict[str, Any] | None = None,
    generation: dict[str, Any] | None = None,
    frame_count: int = 16,
    allow_bare_label: bool = False,
) -> PipelineResult:
    """Execute one traceable prediction from selected input to validated label."""
    total_started = perf_counter()
    timestamps = (
        sampled_timestamps
        if sampled_timestamps is not None
        else sample_timestamps(start_seconds, end_seconds, frame_count)
    )
    payload = build_inference_payload(model, video_data_url, prompt, video_metadata, generation)
    request_started = perf_counter()
    response = client.complete(payload)
    request_duration_ms = (perf_counter() - request_started) * 1000
    label = parse_activity_label(response.content, allow_bare_label=allow_bare_label)
    total_duration_ms = (perf_counter() - total_started) * 1000
    return PipelineResult(
        label=label,
        raw_response=response.content,
        sampled_timestamps=timestamps,
        request_duration_ms=round(request_duration_ms, 1),
        total_duration_ms=round(total_duration_ms, 1),
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
    )
