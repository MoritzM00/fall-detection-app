from typing import Literal

from pydantic import BaseModel, Field, field_validator

from fall_detection.taxonomy import ActivityLabel

type JobState = Literal["queued", "running", "succeeded", "failed", "cancelled", "skipped"]


class VideoAsset(BaseModel):
    """Media metadata exposed by the application API."""

    id: str
    filename: str
    source: Literal["upload", "synthetic", "dataset"]
    storage_key: str | None = None
    duration_seconds: float | None = None
    created_at: str


class GenerationConfiguration(BaseModel):
    """Validated generation parameters saved with one run."""

    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(ge=1, le=4096)


class AnalysisJobCreate(BaseModel):
    """User-selected source and media time range."""

    video_id: str
    start_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    end_seconds: float = Field(default=2, gt=0, allow_inf_nan=False)
    prepared_input_id: str | None = None
    frame_count: int = Field(default=16, ge=2, le=32)
    fps: float = Field(default=7.5, gt=0, le=30, allow_inf_nan=False)
    size: int = Field(default=448, ge=224, le=672)
    model: str | None = Field(default=None, min_length=1)
    prompt_text: str | None = Field(default=None, min_length=1, max_length=16000)
    generation: GenerationConfiguration | None = None

    @field_validator("prompt_text")
    @classmethod
    def validate_prompt(cls, value: str | None) -> str | None:
        """Reject blank prompts while preserving the exact submitted text."""
        if value is not None and not value.strip():
            raise ValueError("Prompt must contain text")
        return value

    @field_validator("generation")
    @classmethod
    def validate_new_token_budget(
        cls, value: GenerationConfiguration | None
    ) -> GenerationConfiguration | None:
        """Keep new runs above a conservative answer budget without changing history."""
        if value is not None and value.max_tokens < 16:
            raise ValueError("New analyses require at least 16 output tokens")
        return value


class PreparationRequest(BaseModel):
    """Editable sampling settings for an exact frame preview."""

    video_id: str
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    frame_count: int = Field(ge=2, le=32)
    fps: float = Field(gt=0, le=30, allow_inf_nan=False)
    size: int = Field(ge=224, le=672)


class PreparedFrame(BaseModel):
    """One selected frame and its real media timestamp."""

    index: int
    requested_seconds: float
    actual_seconds: float
    source_pts: int
    sha256: str
    jpeg_sha256: str


class PreparedInput(BaseModel):
    """Immutable reference to the frames shown and sent for inference."""

    id: str
    video_id: str
    source_sha256: str
    start_seconds: float
    end_seconds: float
    frame_count: int
    fps: float
    size: int
    preprocessing_version: str
    bundle_sha256: str
    frames: list[PreparedFrame]


class DatasetVideoCreate(BaseModel):
    """A selected video from the prepared local OmniFall tree."""

    path: str = Field(min_length=1)


class DatasetVideoOption(BaseModel):
    """Safe browser-facing identity for a prepared dataset video."""

    path: str
    dataset: str
    subject: str
    collection: str
    filename: str


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


class SamplingConfiguration(BaseModel):
    """Validated sampling parameters saved with one run."""

    frames: int = Field(ge=2, le=32)
    fps: float = Field(gt=0, le=30, allow_inf_nan=False)
    resize: int = Field(ge=224, le=672)
    crop: Literal["center"] = "center"
    version: str | None = None
    bundle_sha256: str | None = None


class RunConfiguration(BaseModel):
    """Immutable model, backend, prompt, and input settings for a run."""

    model: str = Field(min_length=1)
    prompt_preset: str
    prompt_text: str
    preprocessing: SamplingConfiguration
    generation: GenerationConfiguration
    backend_kind: Literal["mock", "vllm", "unknown"]
    fixture_version: str | None = None


class AnalysisJob(BaseModel):
    """Durable job lifecycle view returned to clients."""

    id: str
    video_id: str
    configuration_id: str
    prepared_input_id: str | None = None
    state: JobState
    start_seconds: float
    end_seconds: float
    attempt_count: int
    error: str | None
    created_at: str
    updated_at: str
    claim_token: str | None = None
    lease_expires_at: str | None = None
    configuration: RunConfiguration | None = None
    prediction: PredictionResult | None = None
