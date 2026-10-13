"""One input and configuration to one validated activity prediction.

Covers the label taxonomy, deterministic frame sampling, the vLLM-compatible HTTP
transport, and answer parsing. Prompt text lives in ``fall_detection.prompts``. This module must not depend on
web routes, database models, or the frontend; persistence belongs to the application.
"""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

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


class PredictionParseError(ValueError):
    """Raised when generated text does not name exactly one canonical label."""


# Answer markers from the research CoT parser (falldet CoTOutputParser), in its priority
# order. Without chain of thought the research keyword parser looks only for the first.
_ANSWER_MARKERS = (
    re.compile(r"the\s+best\s+answer\s+is\s*[:\-]?\s*", re.IGNORECASE),
    re.compile(r"final\s+answer\s*[:\-]\s*", re.IGNORECASE),
    re.compile(r"the\s+(?:final\s+)?answer\s+is\s*[:\-]?\s*", re.IGNORECASE),
)
# The answer runs to the first period or line break, as in the research keyword parser.
_ANSWER_END = re.compile(r"\.|$", re.MULTILINE)


def _label_pattern(label: str) -> re.Pattern[str]:
    """Match a label as the research keyword parser does, including its word forms.

    ``sit_down`` also matches "sit-down" and "sits down", and every label its -ing and -s
    forms ("walking", "standing up"). As in research the -ing form does not double a
    consonant, so "sitting down" matches ``sitting``, not ``sit_down``.
    """
    parts = [re.escape(part) for part in label.split("_")]
    forms = []
    if len(parts) > 1:
        rest = r"[\s_-]+".join(parts[1:])
        forms += [
            r"[\s_-]+".join(parts),
            rf"{parts[0]}s[\s_-]+{rest}",
            rf"{parts[0]}ing[\s_-]+{rest}",
        ]
    forms += [re.escape(label), rf"{re.escape(label)}ing", rf"{re.escape(label)}s"]
    return re.compile("|".join(rf"\b{form}\b" for form in forms))


_LABEL_PATTERNS = {label: _label_pattern(label) for label in ACTIVITY_LABELS}


def match_label(text: str) -> ActivityLabel:
    """Return the one label named in ``text``, preferring the longer of overlapping matches.

    Proposed application behavior: the research parser takes the longest label found and
    falls back to ``other``; here no label, or two different ones, is a parse failure.
    """
    lowered = text.lower()
    matches = sorted(
        (
            (match.start(), match.end(), label)
            for label, pattern in _LABEL_PATTERNS.items()
            for match in pattern.finditer(lowered)
        ),
        key=lambda item: (item[0] - item[1], item[0]),
    )
    kept: list[tuple[int, int, ActivityLabel]] = []
    for start, end, label in matches:
        if all(end <= other_start or start >= other_end for other_start, other_end, _ in kept):
            kept.append((start, end, label))
    labels = sorted({label for _, _, label in kept}, key=ACTIVITY_LABELS.index)
    if not labels:
        raise PredictionParseError(f"answer names no activity label: {text.strip()[:80]!r}")
    if len(labels) > 1:
        raise PredictionParseError(f"answer names several activity labels: {', '.join(labels)}")
    return labels[0]


@dataclass(frozen=True, slots=True)
class ParsedAnswer:
    """The label and the parts of the response it was read from."""

    label: ActivityLabel
    # Text the model wrote before its answer; None when there was none.
    reasoning: str | None
    # The answer as written, from its marker to the end of the label span.
    answer: str


def parse_answer(raw_response: str, *, chain_of_thought: bool = False) -> ParsedAnswer:
    """Find the answer like the research parsers do and read exactly one label from it.

    With ``chain_of_thought``, reasoning in think tags is set aside first (the opening tag
    may come from the chat template), and any of the research answer markers is accepted.
    Without a marker the whole remaining response must name one label, so a bare label
    also parses.
    """
    reasoning, content = "", raw_response
    if chain_of_thought:
        if "</think>" in raw_response:
            thought, _, content = raw_response.partition("</think>")
            reasoning = thought.partition("<think>")[2] if "<think>" in thought else thought
        elif "<think>" in raw_response:
            raise PredictionParseError("reasoning was not closed before the answer")
    markers = _ANSWER_MARKERS if chain_of_thought else _ANSWER_MARKERS[:1]
    marker = next((found for pattern in markers if (found := pattern.search(content))), None)
    if marker is None:
        answer, before = content.strip(), ""
        span = answer
    else:
        end = _ANSWER_END.search(content, marker.end())
        span_end = end.start() if end else len(content)
        span = content[marker.end() : span_end]
        answer, before = content[marker.start() : span_end].strip(), content[: marker.start()]
    if not span.strip():
        raise PredictionParseError("response contains no answer")
    reasoning = "\n\n".join(part.strip() for part in (reasoning, before) if part.strip())
    return ParsedAnswer(match_label(span), reasoning or None, answer)


def parse_activity_label(raw_response: str, *, chain_of_thought: bool = False) -> ActivityLabel:
    """Return only the label of :func:`parse_answer`."""
    return parse_answer(raw_response, chain_of_thought=chain_of_thought).label


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


def same_origin_client(
    base_url: str, timeout: float, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """Return a client that follows redirects only within the scheme, host and port of base_url.

    Modal answers long requests with a same-origin 303 that must be followed. httpx would
    otherwise follow any redirect, keep custom credential headers such as Modal-Key on a
    cross-origin hop, and resend the frames on a 307 or 308. The check runs before each
    request is sent, so a refused hop sends nothing to the new origin.
    """
    origin = httpx.URL(base_url)

    def check_origin(request: httpx.Request) -> None:
        url = request.url
        if (url.scheme, url.host, url.port) != (origin.scheme, origin.host, origin.port):
            target = f"{url.scheme}://{url.host}" + (f":{url.port}" if url.port else "")
            raise InferenceServiceError(
                f"Refusing redirect to {target}: the endpoint may only redirect within "
                f"{origin.scheme}://{origin.host}" + (f":{origin.port}" if origin.port else "")
            )

    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        transport=transport,
        event_hooks={"request": [check_origin]},
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
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Configure the serving endpoint, credentials and bounded request timeout."""
        self._mock_fixture_identity = mock_fixture_identity
        self._auth_headers = dict(auth_headers or {})
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

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
            # Modal answers requests running past ~150 s with a 303 to poll for the result.
            with same_origin_client(self._base_url, self._timeout_seconds, self._transport) as http:
                response = http.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
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
    reasoning: str | None = None
    answer: str | None = None


def build_inference_payload(
    model: str,
    video_data_url: str,
    prompt: str,
    video_metadata: dict[str, Any] | None = None,
    generation: dict[str, Any] | None = None,
    video_first: bool = True,
) -> dict[str, Any]:
    """Build the online video request shared by mock and vLLM serving.

    The video precedes the prompt text, as in the research zero-shot conversation, unless a
    configuration saved before that change asks for its original text-first order.
    """
    settings = generation or {}
    video = {"type": "video_url", "video_url": {"url": video_data_url}}
    text = {"type": "text", "text": prompt}
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [video, text] if video_first else [text, video],
            }
        ],
        "temperature": settings.get("temperature", 0),
        "max_tokens": settings.get("max_tokens", 32),
        "stream": False,
    }
    # An omitted sampling value leaves the server default in place, as in older runs.
    payload.update(
        {key: settings[key] for key in ("top_p", "top_k", "seed") if settings.get(key) is not None}
    )
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
    prompt: str,
    sampled_timestamps: list[float] | None = None,
    video_metadata: dict[str, Any] | None = None,
    generation: dict[str, Any] | None = None,
    frame_count: int = 16,
    chain_of_thought: bool = False,
    video_first: bool = True,
) -> PipelineResult:
    """Execute one traceable prediction from selected input to validated label."""
    total_started = perf_counter()
    timestamps = (
        sampled_timestamps
        if sampled_timestamps is not None
        else sample_timestamps(start_seconds, end_seconds, frame_count)
    )
    payload = build_inference_payload(
        model, video_data_url, prompt, video_metadata, generation, video_first=video_first
    )
    request_started = perf_counter()
    response = client.complete(payload)
    request_duration_ms = (perf_counter() - request_started) * 1000
    parsed = parse_answer(response.content, chain_of_thought=chain_of_thought)
    total_duration_ms = (perf_counter() - total_started) * 1000
    return PipelineResult(
        label=parsed.label,
        raw_response=response.content,
        sampled_timestamps=timestamps,
        request_duration_ms=round(request_duration_ms, 1),
        total_duration_ms=round(total_duration_ms, 1),
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        reasoning=parsed.reasoning,
        answer=parsed.answer,
    )
