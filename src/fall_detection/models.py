from typing import Any, Literal

from pydantic import BaseModel, Field

from fall_detection.taxonomy import ActivityLabel

type JobState = Literal["queued", "running", "succeeded", "failed", "cancelled", "skipped"]


class VideoAsset(BaseModel):
    """Media metadata exposed by the application API."""

    id: str
    filename: str
    source: Literal["upload", "synthetic"]
    storage_key: str | None = None
    duration_seconds: float | None = None
    created_at: str


class AnalysisJobCreate(BaseModel):
    """User-selected source and media time range."""

    video_id: str
    start_seconds: float = Field(default=0, ge=0)
    end_seconds: float = Field(default=2, gt=0)


class PredictionResult(BaseModel):
    """Validated activity result with model and mock provenance."""

    id: str
    label: ActivityLabel
    raw_response: str
    sampled_timestamps: list[float]
    backend_kind: str
    model: str
    fixture_version: str | None
    request_duration_ms: float
    total_duration_ms: float
    completed_at: str


class AnalysisJob(BaseModel):
    """Durable job lifecycle view returned to clients."""

    id: str
    video_id: str
    configuration_id: str
    state: JobState
    start_seconds: float
    end_seconds: float
    attempt_count: int
    error: str | None
    created_at: str
    updated_at: str
    prediction: PredictionResult | None = None


class InferenceRequest(BaseModel):
    """OpenAI-compatible subset shared with the mock boundary."""

    model: str
    messages: list[dict[str, Any]]
    temperature: float = 0
    max_tokens: int = 32
    stream: bool = False
