from dataclasses import dataclass
from time import perf_counter
from typing import Any

from fall_detection.inference import InferenceClient
from fall_detection.parsing import parse_activity_label
from fall_detection.prompts import THESIS_BASELINE_PROMPT
from fall_detection.sampling import sample_timestamps
from fall_detection.taxonomy import ActivityLabel


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Worker result before application persistence metadata is added."""

    label: ActivityLabel
    raw_response: str
    sampled_timestamps: list[float]
    request_duration_ms: float
    total_duration_ms: float


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
    )
