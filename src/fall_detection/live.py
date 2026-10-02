"""Proposed live camera ingest; frames are application input, not research data."""

import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

from PIL import Image
from pydantic import BaseModel, Field, model_validator

from fall_detection.models import (
    GenerationConfiguration,
    PreparationRequest,
    PreparedInput,
    VideoAsset,
)
from fall_detection.monitoring import Monitoring, SessionCreate
from fall_detection.preparation import (
    crop_image,
    load_prepared_input,
    preparation_path,
    write_bundle,
)
from fall_detection.repository import Repository

LIVE_PREPROCESSING_VERSION = "pillow-live-frames-jpeg-v1"
MAX_BATCH_FRAMES = 64
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_FRAME_EDGE = 4096


class IngestGapError(ValueError):
    """A window has no frame close enough to a sampled timestamp."""


class IngestConflictError(ValueError):
    """A well-formed batch conflicts with stored frames or the session lifecycle."""


class LiveSessionCreate(BaseModel):
    """Inference settings plus immutable capture and scheduling policy."""

    frame_count: int = Field(default=16, ge=2, le=32)
    fps: float = Field(default=7.5, gt=0, le=30, allow_inf_nan=False)
    size: int = Field(default=448, ge=224, le=672)
    model: str | None = Field(default=None, min_length=1)
    prompt_text: str | None = Field(default=None, min_length=1, max_length=16000)
    generation: GenerationConfiguration | None = None
    stride_seconds: float = Field(default=2, gt=0, le=30, allow_inf_nan=False)
    expiration_seconds: float = Field(default=5, gt=0, le=300, allow_inf_nan=False)
    capture_fps: float = Field(default=15, ge=1, le=30, allow_inf_nan=False)
    max_seconds: float = Field(default=3600, gt=0, le=4 * 3600, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_capture(self):
        """Sampling can only select frames the camera actually delivers."""
        if self.fps > self.capture_fps:
            raise ValueError("Sampling FPS cannot exceed the capture FPS")
        return self

    def session(self, video_id: str) -> SessionCreate:
        """Express the live policy as a session whose bound is the maximum length."""
        return SessionCreate(
            video_id=video_id,
            duration_seconds=self.max_seconds,
            **self.model_dump(exclude={"capture_fps", "max_seconds"}),
        )


class FrameMeta(BaseModel):
    """Client sequence and capture time of one uploaded JPEG."""

    seq: int = Field(ge=0)
    capture_seconds: float = Field(ge=0, allow_inf_nan=False)


def gap_tolerance(capture_fps: float) -> float:
    """Largest capture interval still treated as continuous."""
    return 2 / capture_fps


def _frame_path(data_dir: Path, video: VideoAsset, seq: int) -> Path:
    if video.storage_key is None:
        raise ValueError("Live source has no frame log")
    return data_dir / video.storage_key / f"{seq:010d}.jpg"


def _check_jpeg(data: bytes) -> None:
    if not data or len(data) > MAX_FRAME_BYTES:
        raise ValueError("Frames must be non-empty JPEGs of at most 2 MiB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "JPEG":
                raise ValueError("Frames must be JPEG images")
            if max(image.size) > MAX_FRAME_EDGE or min(image.size) == 0:
                raise ValueError("Frame dimensions are out of range")
            image.verify()
    except (OSError, SyntaxError) as exc:
        raise ValueError("Frame is not a readable JPEG") from exc


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}")
    temporary.write_bytes(data)
    temporary.replace(path)


def ingest_frames(
    repository: Repository,
    data_dir: Path,
    session_id: str,
    metadata: list[FrameMeta],
    blobs: list[bytes],
) -> dict:
    """Append one ordered batch and advance the live watermark.

    Batches are idempotent on ``seq``: a resent frame must match what was stored. A
    capture-time jump becomes one explicit ``ingest_gap`` coverage row, and the window
    grid restarts after it so that no admitted window spans missing frames.
    """
    if not 1 <= len(metadata) <= MAX_BATCH_FRAMES or len(metadata) != len(blobs):
        raise ValueError(f"Send 1–{MAX_BATCH_FRAMES} frames with one metadata entry each")
    for previous, current in zip(metadata, metadata[1:], strict=False):
        if current.seq != previous.seq + 1 or current.capture_seconds <= previous.capture_seconds:
            raise ValueError("Frames must have consecutive seq and increasing capture times")
    for blob in blobs:
        _check_jpeg(blob)
    digests = [hashlib.sha256(blob).hexdigest() for blob in blobs]

    monitor = Monitoring(repository)
    # Admit windows completed before this batch, so a gap cannot swallow them.
    monitor.tick()
    with repository._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = connection.execute(
            "SELECT * FROM monitoring_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if session is None:
            raise KeyError(session_id)
        if session["source_kind"] != "live":
            raise ValueError("Only live sessions accept frames")
        video = repository.get_video(session["video_id"])
        if video is None:
            raise ValueError("Live source is missing")
        last = connection.execute(
            "SELECT seq,capture_seconds FROM live_frames WHERE video_id=? ORDER BY seq DESC LIMIT 1",
            (video.id,),
        ).fetchone()
        new: list[tuple[FrameMeta, bytes, str]] = []
        for meta, blob, digest in zip(metadata, blobs, digests, strict=True):
            if last is not None and meta.seq <= last["seq"]:
                stored = connection.execute(
                    "SELECT capture_seconds,sha256 FROM live_frames WHERE video_id=? AND seq=?",
                    (video.id, meta.seq),
                ).fetchone()
                if (
                    stored is None
                    or stored["sha256"] != digest
                    or abs(stored["capture_seconds"] - meta.capture_seconds) > 1e-6
                ):
                    raise IngestConflictError(
                        f"Frame {meta.seq} was already stored with other content"
                    )
                continue
            new.append((meta, blob, digest))
        if new:
            if session["state"] != "running":
                raise IngestConflictError("Session is not running; stop uploading frames")
            first = new[0][0]
            expected = 0 if last is None else last["seq"] + 1
            if first.seq != expected:
                raise IngestConflictError(
                    f"Missing frames before seq {first.seq}; resend from {expected}"
                )
            if last is not None and first.capture_seconds <= last["capture_seconds"]:
                raise IngestConflictError("Capture times must increase across batches")
            if new[-1][0].capture_seconds > session["duration"]:
                raise IngestConflictError("Frames exceed the maximum session length")
            timestamp = repository._timestamp()
            for meta, blob, digest in new:
                _write_atomic(_frame_path(data_dir, video, meta.seq), blob)
                connection.execute(
                    "INSERT INTO live_frames VALUES (?,?,?,?,?)",
                    (video.id, meta.seq, meta.capture_seconds, digest, timestamp),
                )
            origin, next_sequence = session["origin"], session["next_sequence"]
            # Before any frame, the grid's origin stands in for the previous capture.
            reference = session["origin"] if last is None else last["capture_seconds"]
            if first.capture_seconds - reference > gap_tolerance(session["capture_fps"]):
                gap_start = origin + next_sequence * session["stride"]
                connection.execute(
                    """INSERT INTO monitoring_windows
                    (id,session_id,generation,sequence,sequence_end,segment_id,start_seconds,
                     end_seconds,available_seconds,state,reason,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,'skipped','ingest_gap',?)""",
                    (
                        str(uuid4()),
                        session_id,
                        session["generation"],
                        next_sequence,
                        next_sequence,
                        session["segment_id"],
                        min(gap_start, first.capture_seconds),
                        first.capture_seconds,
                        session["position"],
                        timestamp,
                    ),
                )
                next_sequence += 1
                origin = first.capture_seconds - next_sequence * session["stride"]
            connection.execute(
                """UPDATE monitoring_sessions SET position=?,origin=?,next_sequence=?,
                last_frame_at=?,updated_at=? WHERE id=?""",
                (
                    min(new[-1][0].capture_seconds, session["duration"]),
                    origin,
                    next_sequence,
                    timestamp,
                    timestamp,
                    session_id,
                ),
            )
        connection.commit()
    current = monitor.get(session_id)
    with repository._connect() as connection:
        highest = connection.execute(
            "SELECT MAX(seq) FROM live_frames WHERE video_id=?", (current["video_id"],)
        ).fetchone()[0]
    return {
        "accepted": len(new),
        "highest_seq": highest,
        "watermark_seconds": current["position"],
        "state": current["state"],
    }


def prepare_frames(
    repository: Repository,
    video: VideoAsset,
    request: PreparationRequest,
    capture_fps: float,
    data_dir: Path,
    inspection_pngs: bool = True,
) -> PreparedInput:
    """Select causal frames from the log and publish an ordinary prepared bundle."""
    if video.source != "live" or video.id != request.video_id:
        raise ValueError("Frame-log preparation requires a live source")
    end_seconds = request.start_seconds + (request.frame_count - 1) / request.fps
    tolerance = gap_tolerance(capture_fps)
    with repository._connect() as connection:
        rows = connection.execute(
            """SELECT seq,capture_seconds,sha256 FROM live_frames WHERE video_id=?
            AND capture_seconds BETWEEN ? AND ? ORDER BY seq""",
            (video.id, request.start_seconds - tolerance, end_seconds + 1e-6),
        ).fetchall()
    timestamps = [
        request.start_seconds + index / request.fps for index in range(request.frame_count)
    ]
    selected = []
    for requested in timestamps:
        # Nearest frame not after the window end (causal); earlier frame wins ties.
        best = min(
            rows,
            key=lambda row: (abs(row["capture_seconds"] - requested), row["capture_seconds"]),
            default=None,
        )
        if best is None or abs(best["capture_seconds"] - requested) > tolerance:
            raise IngestGapError(f"No captured frame near {requested:.3f} s")
        selected.append(best)
    source_sha256 = hashlib.sha256(
        json.dumps(
            [[row["seq"], row["capture_seconds"], row["sha256"]] for row in selected],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    identity = json.dumps(
        [
            video.id,
            source_sha256,
            request.start_seconds,
            request.frame_count,
            request.fps,
            request.size,
            LIVE_PREPROCESSING_VERSION,
        ],
        separators=(",", ":"),
    )
    prepared_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    if (preparation_path(data_dir, prepared_id) / "manifest.json").is_file():
        return load_prepared_input(data_dir, prepared_id)
    frames = []
    for row in selected:
        data = _frame_path(data_dir, video, row["seq"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Stored live frame failed integrity check")
        with Image.open(io.BytesIO(data)) as image:
            frames.append(
                (crop_image(image.convert("RGB"), request.size), row["capture_seconds"], row["seq"])
            )
    return write_bundle(
        data_dir,
        prepared_id,
        frames,
        timestamps,
        {
            "video_id": video.id,
            "source_sha256": source_sha256,
            "start_seconds": request.start_seconds,
            "end_seconds": end_seconds,
            "frame_count": request.frame_count,
            "fps": request.fps,
            "size": request.size,
            "preprocessing_version": LIVE_PREPROCESSING_VERSION,
        },
        inspection_pngs,
    )
