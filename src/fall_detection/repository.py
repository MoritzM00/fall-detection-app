import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fall_detection.models import AnalysisJob, PredictionResult, VideoAsset
from fall_detection.pipeline import PipelineResult
from fall_detection.prompts import PRESET_ID, THESIS_BASELINE_PROMPT


def utc_now() -> str:
    """Return a stable UTC timestamp for persisted lifecycle events."""
    return datetime.now(UTC).isoformat()


class Repository:
    """SQLite persistence boundary used by API and worker processes."""

    def __init__(self, database_path: Path) -> None:
        """Bind repository operations to one database file."""
        self._database_path = database_path

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the local persistence schema when absent."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    source TEXT NOT NULL CHECK (source IN ('upload', 'synthetic')),
                    storage_key TEXT,
                    duration_seconds REAL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS configurations (
                    id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    prompt_preset TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    preprocessing_json TEXT NOT NULL,
                    generation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL REFERENCES videos(id),
                    configuration_id TEXT NOT NULL REFERENCES configurations(id),
                    state TEXT NOT NULL,
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS predictions (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
                    label TEXT NOT NULL,
                    raw_response TEXT NOT NULL,
                    sampled_timestamps_json TEXT NOT NULL,
                    backend_kind TEXT NOT NULL,
                    model TEXT NOT NULL,
                    fixture_version TEXT,
                    request_duration_ms REAL NOT NULL,
                    total_duration_ms REAL NOT NULL,
                    completed_at TEXT NOT NULL
                );
                """
            )

    def create_sample_video(self) -> VideoAsset:
        """Create or return the deterministic built-in synthetic video record."""
        sample_id = "sample-corridor-v1"
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (sample_id,)).fetchone()
            if row is None:
                created_at = utc_now()
                connection.execute(
                    """INSERT INTO videos
                    (id, filename, source, storage_key, duration_seconds, created_at)
                    VALUES (?, ?, 'synthetic', NULL, 6.0, ?)""",
                    (sample_id, "Synthetic corridor clip", created_at),
                )
                row = connection.execute(
                    "SELECT * FROM videos WHERE id = ?", (sample_id,)
                ).fetchone()
        return self._video_from_row(row)

    def create_uploaded_video(self, filename: str, storage_key: str) -> VideoAsset:
        """Persist uploaded media metadata after its bytes are safely stored."""
        video_id = str(uuid4())
        created_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO videos
                (id, filename, source, storage_key, duration_seconds, created_at)
                VALUES (?, ?, 'upload', ?, NULL, ?)""",
                (video_id, filename, storage_key, created_at),
            )
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return self._video_from_row(row)

    def get_video(self, video_id: str) -> VideoAsset | None:
        """Read one asset without exposing its machine-local path."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return self._video_from_row(row) if row is not None else None

    def create_job(self, video_id: str, start_seconds: float, end_seconds: float) -> AnalysisJob:
        """Snapshot default configuration and enqueue one immutable analysis job."""
        job_id = str(uuid4())
        configuration_id = str(uuid4())
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            video = connection.execute("SELECT id FROM videos WHERE id = ?", (video_id,)).fetchone()
            if video is None:
                connection.rollback()
                raise KeyError(video_id)
            connection.execute(
                """INSERT INTO configurations
                (id, schema_version, model, prompt_preset, prompt_text,
                 preprocessing_json, generation_json, created_at)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?)""",
                (
                    configuration_id,
                    "qwen3-vl-8b-instruct",
                    PRESET_ID,
                    THESIS_BASELINE_PROMPT,
                    json.dumps({"frames": 16, "resize": 448, "crop": "center"}),
                    json.dumps({"temperature": 0, "max_tokens": 32}),
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT INTO jobs
                (id, video_id, configuration_id, state, start_seconds, end_seconds,
                 attempt_count, error, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?, 0, NULL, ?, ?)""",
                (
                    job_id,
                    video_id,
                    configuration_id,
                    start_seconds,
                    end_seconds,
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError("created job could not be read back")
        return job

    def get_job(self, job_id: str) -> AnalysisJob | None:
        """Read a job with its persisted result when completed."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            prediction_row = connection.execute(
                "SELECT * FROM predictions WHERE job_id = ?", (job_id,)
            ).fetchone()
        prediction = self._prediction_from_row(prediction_row) if prediction_row else None
        return AnalysisJob(
            id=row["id"],
            video_id=row["video_id"],
            configuration_id=row["configuration_id"],
            state=row["state"],
            start_seconds=row["start_seconds"],
            end_seconds=row["end_seconds"],
            attempt_count=row["attempt_count"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            prediction=prediction,
        )

    def claim_next_job(self) -> AnalysisJob | None:
        """Atomically claim the oldest queued job for one worker."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM jobs WHERE state = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            timestamp = utc_now()
            connection.execute(
                """UPDATE jobs SET state = 'running', attempt_count = attempt_count + 1,
                updated_at = ? WHERE id = ? AND state = 'queued'""",
                (timestamp, row["id"]),
            )
            connection.commit()
        return self.get_job(row["id"])

    def complete_job(
        self,
        job_id: str,
        result: PipelineResult,
        *,
        backend_kind: str,
        model: str,
        fixture_version: str | None,
    ) -> None:
        """Persist one prediction idempotently and mark its job successful."""
        completed_at = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO predictions
                (id, job_id, label, raw_response, sampled_timestamps_json, backend_kind,
                 model, fixture_version, request_duration_ms, total_duration_ms, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    job_id,
                    result.label,
                    result.raw_response,
                    json.dumps(result.sampled_timestamps),
                    backend_kind,
                    model,
                    fixture_version,
                    result.request_duration_ms,
                    result.total_duration_ms,
                    completed_at,
                ),
            )
            connection.execute(
                "UPDATE jobs SET state = 'succeeded', error = NULL, updated_at = ? WHERE id = ?",
                (completed_at, job_id),
            )
            connection.commit()

    def fail_job(self, job_id: str, error: str) -> None:
        """Persist an explicit processing failure without inventing a label."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET state = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (error, utc_now(), job_id),
            )

    @staticmethod
    def _video_from_row(row: sqlite3.Row) -> VideoAsset:
        return VideoAsset(
            id=row["id"],
            filename=row["filename"],
            source=row["source"],
            storage_key=row["storage_key"],
            duration_seconds=row["duration_seconds"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _prediction_from_row(row: sqlite3.Row) -> PredictionResult:
        return PredictionResult(
            id=row["id"],
            label=row["label"],
            raw_response=row["raw_response"],
            sampled_timestamps=json.loads(row["sampled_timestamps_json"]),
            backend_kind=row["backend_kind"],
            model=row["model"],
            fixture_version=row["fixture_version"],
            request_duration_ms=row["request_duration_ms"],
            total_duration_ms=row["total_duration_ms"],
            completed_at=row["completed_at"],
        )
