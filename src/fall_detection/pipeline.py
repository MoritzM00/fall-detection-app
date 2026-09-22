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


def build_inference_payload(model: str, video_data_url: str, prompt: str) -> dict[str, Any]:
    """Build the single request shape shared by mock and future vLLM serving."""
    return {
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
        "temperature": 0,
        "max_tokens": 32,
        "stream": False,
    }


def run_pipeline(
    *,
    client: InferenceClient,
    model: str,
    video_data_url: str,
    start_seconds: float,
    end_seconds: float,
) -> PipelineResult:
    """Execute one traceable prediction from selected input to validated label."""
    total_started = perf_counter()
    timestamps = sample_timestamps(start_seconds, end_seconds)
    payload = build_inference_payload(model, video_data_url, THESIS_BASELINE_PROMPT)
    request_started = perf_counter()
    response = client.complete(payload)
    request_duration_ms = (perf_counter() - request_started) * 1000
    label = parse_activity_label(response.content)
    total_duration_ms = (perf_counter() - total_started) * 1000
    return PipelineResult(
        label=label,
        raw_response=response.content,
        sampled_timestamps=timestamps,
        request_duration_ms=round(request_duration_ms, 1),
        total_duration_ms=round(total_duration_ms, 1),
    )
