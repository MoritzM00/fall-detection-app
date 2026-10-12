"""Recorded replay and live camera monitoring session routes."""

import json
import sqlite3
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from fall_detection import live
from fall_detection.config import Settings
from fall_detection.live import FrameMeta, IngestConflictError, LiveSessionCreate, ingest_frames
from fall_detection.monitoring import (
    Monitoring,
    SessionCommand,
    SessionConfiguration,
    SessionCreate,
)
from fall_detection.prediction import InferenceClient, InferenceServiceError
from fall_detection.repository import Repository
from fall_detection.storage import storage_lock


def monitoring_router(settings: Settings, repository: Repository) -> APIRouter:
    """Build session routes bound to one application's settings and database."""
    router = APIRouter()

    def monitoring_configuration(request: SessionConfiguration):
        if request.model is not None and request.model != settings.inference_model:
            raise ValueError("Choose a model advertised by capabilities")
        fixture = None
        if settings.backend_kind == "mock":
            fixture = InferenceClient(
                settings.inference_base_url, timeout_seconds=settings.request_timeout_seconds
            ).discover_mock_identity()
        return Monitoring.configuration(
            request, settings.inference_model, settings.backend_kind, fixture
        )

    @router.post("/monitoring-sessions", status_code=201)
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

    @router.post("/monitoring-sessions/live", status_code=201)
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

    @router.post("/monitoring-sessions/{session_id}/frames")
    def ingest_live_frames(
        session_id: str,
        run_id: Annotated[str, Form()],
        metadata: Annotated[str, Form()],
        frames: Annotated[list[UploadFile], File()],
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
                    repository, settings.data_dir, session_id, run_id, metas, blobs
                )
        except KeyError as exc:
            raise HTTPException(404, "Session not found") from exc
        except IngestConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/monitoring-sessions")
    def list_monitoring_sessions() -> list[dict]:
        """List retained sessions for refresh recovery."""
        with repository._connect() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM monitoring_sessions ORDER BY created_at DESC LIMIT 100"
                )
            ]
        monitor = Monitoring(repository)
        return [monitor.get(identity) for identity in ids]

    @router.get("/monitoring-sessions/{session_id}")
    def get_monitoring_session(session_id: str) -> dict:
        """Read session lifecycle and latest eligible job identity."""
        try:
            return Monitoring(repository).get(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Session not found") from exc

    @router.get("/monitoring-sessions/{session_id}/windows")
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

    @router.post("/monitoring-sessions/{session_id}/commands")
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
