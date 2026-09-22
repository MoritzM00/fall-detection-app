import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

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
    VideoAsset,
)
from fall_detection.prompts import PRESET_ID, THESIS_BASELINE_PROMPT
from fall_detection.repository import Repository
from fall_detection.taxonomy import ACTIVITY_LABELS

settings = Settings.from_env()
repository = Repository(settings.database_path)
dataset_video_root = settings.data_dir / "omnifall" / "videos"


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
    storage_key = f"media/{uuid4()}{suffix}"
    target = settings.data_dir / storage_key
    with target.open("wb") as destination:
        shutil.copyfileobj(file.file, destination)
    return repository.create_uploaded_video(file.filename or target.name, storage_key)


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
    try:
        return repository.create_job(request.video_id, request.start_seconds, request.end_seconds)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Video not found") from exc


@app.get("/analysis-jobs/{job_id}", response_model=AnalysisJob)
def get_analysis_job(job_id: str) -> AnalysisJob:
    """Read current job state and its result when available."""
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return job
