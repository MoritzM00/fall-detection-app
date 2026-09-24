from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import av
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from fall_detection.config import Settings
from fall_detection.media import list_dataset_video_paths, resolve_dataset_video
from fall_detection.models import (
    AnalysisJob,
    AnalysisJobCreate,
    DatasetVideoCreate,
    DatasetVideoOption,
    PreparationRequest,
    PreparedInput,
    VideoAsset,
)
from fall_detection.preparation import load_prepared_input, preparation_path, prepare_video
from fall_detection.preparation_queue import PreparationBusyError, PreparationCoordinator
from fall_detection.prompts import PRESET_ID, THESIS_BASELINE_PROMPT
from fall_detection.repository import Repository
from fall_detection.taxonomy import ACTIVITY_LABELS

settings = Settings.from_env()
repository = Repository(settings.database_path)
dataset_video_root = settings.data_dir / "omnifall" / "videos"
preparation_coordinator = PreparationCoordinator(
    settings.preparation_slots, settings.preparation_wait_seconds
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Initialize durable local state for the API process."""
    repository.initialize()
    yield


app = FastAPI(title="Fall Detection API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Report API process health."""
    return {"status": "ok"}


@app.get("/capabilities")
def capabilities() -> dict[str, object]:
    """Expose only settings supported by the current deployment."""
    return {
        "labels": ACTIVITY_LABELS,
        "models": [settings.inference_model],
        "backend_kind": settings.backend_kind,
        "simulated": settings.backend_kind == "mock",
        "prompt_preset": {"id": PRESET_ID, "prompt": THESIS_BASELINE_PROMPT},
        "preprocessing": {"frame_count": 16, "size": 448, "crop": "center"},
    }


@app.post("/videos/sample", response_model=VideoAsset)
def create_sample_video() -> VideoAsset:
    """Create or return the deterministic synthetic sample asset."""
    return repository.create_sample_video()


@app.get("/dataset-videos", response_model=list[DatasetVideoOption])
def list_dataset_videos() -> list[DatasetVideoOption]:
    """List videos already prepared in the local OmniFall directory."""
    options: list[DatasetVideoOption] = []
    for relative_path in list_dataset_video_paths(dataset_video_root):
        parts = Path(relative_path).parts
        if len(parts) < 5:
            continue
        options.append(
            DatasetVideoOption(
                path=relative_path,
                dataset=parts[0],
                subject=parts[-3],
                collection=parts[-2],
                filename=parts[-1],
            )
        )
    return options


@app.post("/videos/dataset", response_model=VideoAsset)
def create_dataset_video(request: DatasetVideoCreate) -> VideoAsset:
    """Register one prepared dataset video without copying its bytes."""
    try:
        path = resolve_dataset_video(dataset_video_root, request.path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    storage_key = path.relative_to(settings.data_dir.resolve()).as_posix()
    return repository.create_dataset_video(path.name, storage_key)


@app.post("/videos", response_model=VideoAsset, status_code=status.HTTP_201_CREATED)
def upload_video(file: Annotated[UploadFile, File()]) -> VideoAsset:
    """Store a supported upload and persist its relative storage identity."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=415, detail="Supported formats: MP4, MOV, WebM, MKV")
    media_dir = settings.data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    if not media_dir.resolve().is_relative_to(settings.data_dir.resolve()):
        raise HTTPException(status_code=400, detail="Upload storage path is unsafe")
    storage_key = f"media/{uuid4()}{suffix}"
    target = settings.data_dir / storage_key
    copied = 0
    try:
        with target.open("xb") as destination:
            while chunk := file.file.read(1024 * 1024):
                copied += len(chunk)
                if copied > settings.upload_max_bytes:
                    raise HTTPException(
                        status_code=413, detail="Upload exceeds the configured byte limit"
                    )
                destination.write(chunk)
        return repository.create_uploaded_video(file.filename or target.name, storage_key)
    except Exception:
        target.unlink(missing_ok=True)
        raise


@app.get("/videos/{video_id}", response_model=VideoAsset)
def get_video(video_id: str) -> VideoAsset:
    """Read persisted video metadata."""
    video = repository.get_video(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@app.get("/videos/{video_id}/media")
def get_video_media(video_id: str) -> FileResponse:
    """Stream an uploaded video's bytes for browser playback."""
    video = repository.get_video(video_id)
    if video is None or video.storage_key is None:
        raise HTTPException(status_code=404, detail="Video media not found")
    path = settings.data_dir / video.storage_key
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Video media not found")
    return FileResponse(path, filename=video.filename)


@app.post("/analysis-jobs", response_model=AnalysisJob, status_code=status.HTTP_202_ACCEPTED)
def create_analysis_job(request: AnalysisJobCreate) -> AnalysisJob:
    """Validate a selected range and persist a queued analysis job."""
    if request.end_seconds <= request.start_seconds:
        raise HTTPException(status_code=422, detail="End time must be after start time")
    if request.end_seconds - request.start_seconds > 30:
        raise HTTPException(status_code=422, detail="MVP selections are limited to 30 seconds")
    video = repository.get_video(request.video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.source == "synthetic":
        expected_end = request.start_seconds + (request.frame_count - 1) / request.fps
        if abs(expected_end - request.end_seconds) > 0.001:
            raise HTTPException(
                status_code=422,
                detail="Synthetic sampling settings do not match the selected window",
            )
        if video.duration_seconds is not None and request.end_seconds > video.duration_seconds:
            raise HTTPException(
                status_code=422, detail="Selected window exceeds the synthetic clip"
            )
    prepared = None
    if video.source != "synthetic":
        if request.prepared_input_id is None:
            raise HTTPException(status_code=422, detail="Prepare frames before analyzing")
        try:
            prepared = load_prepared_input(settings.data_dir, request.prepared_input_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if (
            prepared.video_id != video.id
            or abs(prepared.start_seconds - request.start_seconds) > 0.001
            or abs(prepared.end_seconds - request.end_seconds) > 0.001
            or prepared.frame_count != request.frame_count
            or abs(prepared.fps - request.fps) > 0.000001
            or prepared.size != request.size
        ):
            raise HTTPException(
                status_code=422, detail="Prepared frames do not match the selected video window"
            )
    try:
        return repository.create_job(
            request.video_id,
            request.start_seconds,
            request.end_seconds,
            model=settings.inference_model,
            backend_kind=settings.backend_kind,
            fixture_version=settings.mock_fixture_version,
            prepared_input_id=prepared.id if prepared else None,
            preprocessing=(
                {
                    "frames": prepared.frame_count,
                    "fps": prepared.fps,
                    "resize": prepared.size,
                    "crop": "center",
                    "version": prepared.preprocessing_version,
                    "bundle_sha256": prepared.bundle_sha256,
                }
                if prepared
                else {
                    "frames": request.frame_count,
                    "fps": request.fps,
                    "resize": request.size,
                    "crop": "center",
                }
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Video not found") from exc


@app.post("/prepared-inputs", response_model=PreparedInput)
def create_prepared_input(request: PreparationRequest) -> PreparedInput:
    """Decode and retain the exact selected frames before analysis."""
    video = repository.get_video(request.video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.source == "synthetic":
        raise HTTPException(status_code=422, detail="Synthetic sample has no decodable frames")
    try:
        key = (video.id, request.start_seconds, request.frame_count, request.fps, request.size)
        return preparation_coordinator.run(
            key, lambda: prepare_video(video, request, settings.data_dir, settings.inspection_pngs)
        )
    except PreparationBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "3"}) from exc
    except (ValueError, OSError, av.error.FFmpegError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/prepared-inputs/{prepared_id}", response_model=PreparedInput)
def get_prepared_input(prepared_id: str) -> PreparedInput:
    """Read a prepared frame manifest."""
    try:
        return load_prepared_input(settings.data_dir, prepared_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/prepared-inputs/{prepared_id}/frames/{index}")
def get_prepared_frame(prepared_id: str, index: int) -> FileResponse:
    """Serve the exact JPEG frame sent to inference."""
    try:
        prepared = load_prepared_input(settings.data_dir, prepared_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not 0 <= index < prepared.frame_count:
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(preparation_path(settings.data_dir, prepared_id) / f"{index:02d}.jpg")


@app.get("/analysis-jobs/{job_id}", response_model=AnalysisJob)
def get_analysis_job(job_id: str) -> AnalysisJob:
    """Read current job state and its result when available."""
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return job


@app.get("/analysis-jobs", response_model=list[AnalysisJob])
def list_analysis_jobs(limit: int = 20) -> list[AnalysisJob]:
    """Return active and recent persisted runs for refresh recovery."""
    try:
        return repository.list_jobs(limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/analysis-jobs/{job_id}/retry", response_model=AnalysisJob)
def retry_analysis_job(job_id: str) -> AnalysisJob:
    """Explicitly requeue a failed run without changing its configuration."""
    if repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    try:
        return repository.retry_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
