import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fall_detection.prediction import ActivityLabel

type JobState = Literal["queued", "running", "succeeded", "failed", "cancelled", "skipped"]


class VideoAsset(BaseModel):
    """Media metadata exposed by the application API."""

    id: str
    filename: str
    source: Literal["upload", "synthetic", "dataset", "live"]
    storage_key: str | None = None
    duration_seconds: float | None = None
    created_at: str


# Proposed application behavior, not research-verified: a conservative upper bound of the
# context one request needs on the served Qwen3-VL model, checked at submission so an
# impossible configuration fails with 422 instead of a 400 from the GPU after a cold start.
# Qwen3-VL turns every 2 frames into one temporal patch of (side / 32)² tokens (16 px
# patches, 2 × 2 merge), with the side rounded up. The processor may upscale small frames,
# so the side counts as at least 12 merged patches (384 px). Each temporal patch also carries a
# timestamp and vision markers (budgeted at 16 tokens), the chat template adds a few
# tokens, and prompt text is counted as one token per UTF-8 byte, which bounds any
# byte-level BPE tokenizer from above.
_PATCH_PIXELS = 32
_MIN_PATCH_SIDE = 12
_FRAMES_PER_TEMPORAL_PATCH = 2
_TOKENS_PER_TEMPORAL_PATCH_OVERHEAD = 16
_CHAT_TEMPLATE_TOKENS = 32


class ContextWindowError(ValueError):
    """Raised when a configuration cannot fit the served model's context window."""


def estimate_request_tokens(frame_count: int, size: int, prompt_text: str, max_tokens: int) -> int:
    """Return a conservative upper bound of prompt plus completion tokens for one request."""
    side = max(math.ceil(size / _PATCH_PIXELS), _MIN_PATCH_SIDE)
    temporal_patches = math.ceil(frame_count / _FRAMES_PER_TEMPORAL_PATCH)
    visual = temporal_patches * (side * side + _TOKENS_PER_TEMPORAL_PATCH_OVERHEAD)
    text = len(prompt_text.encode()) + _CHAT_TEMPLATE_TOKENS
    return visual + text + max_tokens


def check_context_window(
    frame_count: int, size: int, prompt_text: str, max_tokens: int, max_model_len: int | None
) -> None:
    """Reject a configuration whose estimated request exceeds the served context window."""
    if max_model_len is None:
        return
    estimate = estimate_request_tokens(frame_count, size, prompt_text, max_tokens)
    if estimate > max_model_len:
        raise ContextWindowError(
            f"This configuration may need up to {estimate} tokens ({frame_count} frames at "
            f"{size} px, prompt and {max_tokens} max tokens), but the served model's context "
            f"window is {max_model_len} tokens. Use fewer frames, a smaller crop, a shorter "
            "prompt or fewer max tokens."
        )


class GenerationConfiguration(BaseModel):
    """Validated generation parameters saved with one run.

    ``None`` means the request omitted the value and the server applied its own default;
    runs saved before these fields existed read back as ``None``. Frozen because presets
    share one instance across requests.
    """

    model_config = ConfigDict(frozen=True)

    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(ge=1, le=4096)
    top_p: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    # -1 disables top-k filtering, as in vLLM and the research sampling configs.
    top_k: int | None = Field(default=None, ge=-1, le=1000)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)

    @field_validator("top_k")
    @classmethod
    def validate_top_k(cls, value: int | None) -> int | None:
        """Reject 0, which vLLM versions disagree on; -1 is the explicit way to disable."""
        if value == 0:
            raise ValueError("top_k must be -1 (disabled) or at least 1")
        return value


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
    # Reasoning precedes the final answer line; omitted prompt and generation then
    # resolve to the chain-of-thought preset.
    chain_of_thought: bool = False
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
    # Server-reported usage; None when the backend did not report it.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


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
    chain_of_thought: bool = False
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
