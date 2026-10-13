"""Recorded replay and live camera monitoring session routes."""

import json
import sqlite3
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from fall_detection import live
from fall_detection.config import Settings
from fall_detection.live import FrameMeta, IngestConflictError, LiveSessionCreate, ingest_frames
from fall_detection.models import ContextWindowError, check_context_window
from fall_detection.monitoring import (
    Monitoring,
    SessionCommand,
    SessionConfiguration,
    SessionCreate,
)
from fall_detection.prediction import (
    InferenceClient,
    InferenceServiceError,
)
from fall_detection.prompts import default_preset
from fall_detection.repository import Repository
from fall_detection.scheduling import SessionState, SourceKind, WindowState
from fall_detection.storage import storage_lock


class MonitoringSessionView(BaseModel):
    """A session as clients read it: every stored column plus derived read fields.

    Internal scheduling columns (``origin``, ``next_sequence``, ``duration_verified``,
    ``last_frame_at``) stay in the contract because tests and diagnostics read them;
    removing one is now an explicit change here rather than a side effect of SQL.
    """

    id: str
    schema_version: int
    video_id: str
    state: SessionState
    generation: int
    segment_id: str
    position: float
    duration: float
    duration_verified: int
    origin: float
    next_sequence: int
    stride: float
    expiration: float
    created_at: str
    updated_at: str
    recovery_reason: str | None
    source_kind: SourceKind
    capture_fps: float | None
    last_frame_at: str | None
    # The saved segment configuration, exactly as stored; legacy snapshots may lack fields.
    configuration: dict[str, Any]
    latest_job_id: str | None


class MonitoringWindowView(BaseModel):
    """One coverage record with its paging cursor; ``reason`` may be a freeform diagnostic."""

    cursor: int
    id: str
    schema_version: int
    session_id: str
    generation: int
    sequence: int
    sequence_end: int
    segment_id: str
    start_seconds: float
    end_seconds: float
    available_seconds: float
    state: WindowState
    reason: str | None
    prepared_input_id: str | None
    job_id: str | None
    created_at: str


class FrameBatchView(BaseModel):
    """Acknowledgement of one ingested frame batch."""

    accepted: int
    run_seq_high: int | None
    watermark_seconds: float
    state: SessionState


def monitoring_router(settings: Settings, repository: Repository) -> APIRouter:
    """Build session routes bound to one application's settings and database."""
    router = APIRouter()

    def monitoring_configuration(request: SessionConfiguration):
        if request.model is not None and request.model != settings.inference_model:
            raise ValueError("Choose a model advertised by capabilities")
        preset = default_preset(request.chain_of_thought)
        try:
            check_context_window(
                request.frame_count,
                request.size,
                request.prompt_text or preset.prompt,
                (request.generation or preset.generation).max_tokens,
                settings.max_model_len,
            )
        except ContextWindowError as exc:
            # 422 for every route, including configure, which reports other errors as 409.
            raise HTTPException(422, str(exc)) from exc
        fixture = None
        if settings.backend_kind == "mock":
            fixture = InferenceClient(
                settings.inference_base_url, timeout_seconds=settings.request_timeout_seconds
            ).discover_mock_identity()
        return Monitoring.configuration(
            request, settings.inference_model, settings.backend_kind, fixture
        )

    @router.post("/monitoring-sessions", status_code=201, response_model=MonitoringSessionView)
    def create_monitoring_session(request: SessionCreate) -> dict:
        """Persist replay policy without decoding in the API request."""
        try:
            video = repository.get_video(request.video_id)
            if video is None:
                raise KeyError(request.video_id)
            if (
                video.duration_seconds is not None
                and request.duration_seconds > video.duration_seconds
            ):
                raise ValueError("Playback bound exceeds known source duration")
            if video.source == "synthetic" and settings.backend_kind != "mock":
                raise ValueError("Synthetic replay requires mock serving")
            config = monitoring_configuration(request)
            with storage_lock(settings.data_dir, exclusive=False):
                return Monitoring(repository).create(request, config)
        except KeyError as exc:
            raise HTTPException(404, "Video not found") from exc
        except InferenceServiceError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/monitoring-sessions/live", status_code=201, response_model=MonitoringSessionView)
    def create_live_session(request: LiveSessionCreate) -> dict:
        """Register a camera frame log and a paused session that follows it."""
        try:
            source = request.session(str(uuid4()))
            config = monitoring_configuration(source)
            with storage_lock(settings.data_dir, exclusive=False):
                return Monitoring(repository).create(
                    source, config, capture_fps=request.capture_fps
                )
        except InferenceServiceError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/monitoring-sessions/{session_id}/frames", response_model=FrameBatchView)
    def ingest_live_frames(
        session_id: str,
        run_id: Annotated[str, Form()],
        metadata: Annotated[str, Form()],
        frames: Annotated[list[UploadFile], File()],
        run_order: Annotated[int | None, Form(ge=0)] = None,
    ) -> dict:
        """Append an ordered, idempotent batch of one capture run's JPEG frames."""
        try:
            metas = [FrameMeta.model_validate(item) for item in json.loads(metadata)]
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                422, "metadata must be a JSON list of {run_seq, capture_seconds}"
            ) from exc
        if not 1 <= len(frames) <= live.MAX_BATCH_FRAMES:
            raise HTTPException(422, f"Send 1–{live.MAX_BATCH_FRAMES} frames per batch")
        blobs = [frame.file.read(live.MAX_FRAME_BYTES + 1) for frame in frames]
        try:
            with storage_lock(settings.data_dir, exclusive=False):
                return ingest_frames(
                    repository, settings.data_dir, session_id, run_id, metas, blobs, run_order
                )
        except KeyError as exc:
            raise HTTPException(404, "Session not found") from exc
        except IngestConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/monitoring-sessions", response_model=list[MonitoringSessionView])
    def list_monitoring_sessions() -> list[dict]:
        """List retained sessions for refresh recovery."""
        return Monitoring(repository).recent(100)

    @router.get("/monitoring-sessions/{session_id}", response_model=MonitoringSessionView)
    def get_monitoring_session(session_id: str) -> dict:
        """Read session lifecycle and latest eligible job identity."""
        try:
            return Monitoring(repository).get(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Session not found") from exc

    @router.get(
        "/monitoring-sessions/{session_id}/windows", response_model=list[MonitoringWindowView]
    )
    def monitoring_history(
        session_id: str, limit: int = 100, before: int | None = None
    ) -> list[dict]:
        """Page history and explicit coverage records."""
        try:
            return Monitoring(repository).history(session_id, limit, before)
        except KeyError as exc:
            raise HTTPException(404, "Session not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/monitoring-sessions/{session_id}/commands", response_model=MonitoringSessionView)
    def monitoring_command(session_id: str, request: SessionCommand) -> dict:
        """Apply explicit playback updates and lifecycle commands."""
        try:
            session = Monitoring(repository).get(session_id)
            if request.action == "configure":
                if request.configuration is None:
                    raise ValueError("Configure requires a configuration")
                if request.configuration.video_id != session["video_id"]:
                    raise ValueError("Configuration cannot change the source")
            config = (
                monitoring_configuration(request.configuration)
                if request.action == "configure" and request.configuration
                else None
            )
            return Monitoring(repository).command(session_id, request, config)
        except KeyError as exc:
            raise HTTPException(404, "Session not found") from exc
        except InferenceServiceError as exc:
            raise HTTPException(503, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            if "monitoring_sessions.state" in str(exc):
                raise HTTPException(
                    409,
                    "Another monitoring session is running. Pause or stop it before starting this one.",
                ) from exc
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
