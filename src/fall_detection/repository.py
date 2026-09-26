import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from uuid import NAMESPACE_URL, uuid4, uuid5

from fall_detection.models import (
    AnalysisJob,
    GenerationConfiguration,
    PredictionResult,
    RunConfiguration,
    SamplingConfiguration,
    VideoAsset,
)
from fall_detection.pipeline import PipelineResult
from fall_detection.prompts import PRESET_ID, THESIS_BASELINE_PROMPT


def utc_now() -> str:
    """Return a stable UTC timestamp for persisted lifecycle events."""
    return datetime.now(UTC).isoformat()


class Repository:
    """SQLite persistence boundary used by API and worker processes."""

    def __init__(self, database_path: Path, clock: Callable[[], datetime] | None = None) -> None:
        """Bind repository operations to one database file."""
        self._database_path = database_path
        self._clock = clock or (lambda: datetime.now(UTC))

    def _timestamp(self) -> str:
        return self._clock().astimezone(UTC).isoformat()

    @contextmanager
    def _connect(self, *, foreign_keys: bool = True):
        connection = sqlite3.connect(self._database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
            # Changing journal mode can return SQLITE_BUSY immediately, even with
            # busy_timeout configured, when another startup connection holds a lock.
            deadline = monotonic() + 10
            while True:
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if (
                        getattr(exc, "sqlite_errorcode", 0) & 0xFF != sqlite3.SQLITE_BUSY
                        or monotonic() >= deadline
                    ):
                        raise
                    sleep(0.01)
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create and migrate the schema under a database-wide write lock."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(foreign_keys=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for statement in (
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    source TEXT NOT NULL CHECK (source IN ('upload', 'synthetic', 'dataset')),
                    storage_key TEXT,
                    duration_seconds REAL,
                    created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS configurations (
                    id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    prompt_preset TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    preprocessing_json TEXT NOT NULL,
                    generation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL REFERENCES videos(id),
                    configuration_id TEXT NOT NULL REFERENCES configurations(id),
                    prepared_input_id TEXT,
                    state TEXT NOT NULL,
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS predictions (
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
                )""",
            ):
                connection.execute(statement)
            videos_schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'videos'"
            ).fetchone()["sql"]
            if "'dataset'" not in videos_schema:
                # SQLite cannot alter a CHECK constraint. Preserve the old rows
                # while moving the table name under the same transaction.
                connection.execute("""CREATE TABLE videos_new (
                        id TEXT PRIMARY KEY,
                        filename TEXT NOT NULL,
                        source TEXT NOT NULL
                            CHECK (source IN ('upload', 'synthetic', 'dataset')),
                        storage_key TEXT,
                        duration_seconds REAL,
                        created_at TEXT NOT NULL
                    )""")
                connection.execute("""INSERT INTO videos_new
                        (id, filename, source, storage_key, duration_seconds, created_at)
                    SELECT id, filename, source, storage_key, duration_seconds, created_at
                    FROM videos""")
                connection.execute("DROP TABLE videos")
                connection.execute("ALTER TABLE videos_new RENAME TO videos")
            job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "prepared_input_id" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN prepared_input_id TEXT")
            if "claim_token" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN claim_token TEXT")
            if "lease_expires_at" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT")
            config_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(configurations)")
            }
            if "backend_kind" not in config_columns:
                connection.execute(
                    "ALTER TABLE configurations ADD COLUMN backend_kind TEXT NOT NULL DEFAULT 'unknown'"
                )
            if "fixture_version" not in config_columns:
                connection.execute("ALTER TABLE configurations ADD COLUMN fixture_version TEXT")
            connection.execute(
                """UPDATE jobs SET state = 'failed', error =
                'Worker interrupted before claim ownership was recorded. Retry this run.',
                updated_at = ? WHERE state = 'running' AND claim_token IS NULL""",
                (self._timestamp(),),
            )
            connection.execute("PRAGMA user_version = 2")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Migration would violate foreign keys")
            connection.commit()

    def create_sample_video(self) -> VideoAsset:
        """Create or return the deterministic built-in synthetic video record."""
        sample_id = "sample-corridor-v1"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO videos
                (id, filename, source, storage_key, duration_seconds, created_at)
                VALUES (?, ?, 'synthetic', NULL, 6.0, ?)
                ON CONFLICT(id) DO NOTHING""",
                (sample_id, "Synthetic corridor clip", utc_now()),
            )
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (sample_id,)).fetchone()
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

    def create_dataset_video(self, filename: str, storage_key: str) -> VideoAsset:
        """Create or return a stable record for a prepared local dataset video."""
        video_id = str(uuid5(NAMESPACE_URL, f"fall-detection-app:{storage_key}"))
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO videos
                (id, filename, source, storage_key, duration_seconds, created_at)
                VALUES (?, ?, 'dataset', ?, NULL, ?)
                ON CONFLICT(id) DO NOTHING""",
                (video_id, filename, storage_key, utc_now()),
            )
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return self._video_from_row(row)

    def get_video(self, video_id: str) -> VideoAsset | None:
        """Read one asset without exposing its machine-local path."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return self._video_from_row(row) if row is not None else None

    def create_job(
        self,
        video_id: str,
        start_seconds: float,
        end_seconds: float,
        *,
        model: str = "qwen3-vl-8b-instruct",
        prepared_input_id: str | None = None,
        preprocessing: dict[str, object] | None = None,
        generation: dict[str, object] | None = None,
        prompt_text: str = THESIS_BASELINE_PROMPT,
        backend_kind: str = "mock",
        fixture_version: str | None = "sample-v1",
    ) -> AnalysisJob:
        """Snapshot selected configuration and enqueue one immutable analysis job."""
        job_id = str(uuid4())
        configuration_id = str(uuid4())
        timestamp = utc_now()
        sampling = SamplingConfiguration.model_validate(
            preprocessing or {"frames": 16, "fps": 7.5, "resize": 448, "crop": "center"}
        )
        generation_settings = GenerationConfiguration.model_validate(
            generation or {"temperature": 0, "max_tokens": 32}
        )
        if backend_kind not in {"mock", "vllm"}:
            raise ValueError("Unsupported backend kind")
        if not prompt_text.strip() or len(prompt_text) > 16000:
            raise ValueError("Prompt must contain 1–16000 characters")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            video = connection.execute("SELECT id FROM videos WHERE id = ?", (video_id,)).fetchone()
            if video is None:
                connection.rollback()
                raise KeyError(video_id)
            connection.execute(
                """INSERT INTO configurations
                (id, schema_version, model, prompt_preset, prompt_text,
                 preprocessing_json, generation_json, created_at, backend_kind, fixture_version)
                VALUES (?, 2, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    configuration_id,
                    model,
                    PRESET_ID if prompt_text == THESIS_BASELINE_PROMPT else "custom",
                    prompt_text,
                    sampling.model_dump_json(),
                    generation_settings.model_dump_json(),
                    timestamp,
                    backend_kind,
                    fixture_version if backend_kind == "mock" else None,
                ),
            )
            connection.execute(
                """INSERT INTO jobs
                (id, video_id, configuration_id, prepared_input_id, state, start_seconds, end_seconds,
                 attempt_count, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?, 0, NULL, ?, ?)""",
                (
                    job_id,
                    video_id,
                    configuration_id,
                    prepared_input_id,
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

    def get_inference_configuration(self, configuration_id: str) -> tuple[str, str]:
        """Read the immutable model and prompt selected when a job was queued."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT model, prompt_text FROM configurations WHERE id = ?",
                (configuration_id,),
            ).fetchone()
        if row is None:
            raise ValueError("job configuration no longer exists")
        return row["model"], row["prompt_text"]

    def get_configuration(self, configuration_id: str) -> RunConfiguration:
        """Load and validate one saved run configuration."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM configurations WHERE id = ?", (configuration_id,)
            ).fetchone()
        if row is None:
            raise ValueError("job configuration no longer exists")
        preprocessing = json.loads(row["preprocessing_json"])
        # Historic snapshots did not contain FPS. Their backend is deliberately unknown.
        preprocessing.setdefault("fps", 7.5)
        return RunConfiguration(
            model=row["model"],
            prompt_preset=row["prompt_preset"],
            prompt_text=row["prompt_text"],
            preprocessing=SamplingConfiguration.model_validate(preprocessing),
            generation=GenerationConfiguration.model_validate_json(row["generation_json"]),
            backend_kind=row["backend_kind"],
            fixture_version=row["fixture_version"],
        )

    def get_job(self, job_id: str) -> AnalysisJob | None:
        """Read a job with its persisted result when completed."""
        self.recover_expired()
        with self._connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            prediction_row = connection.execute(
                "SELECT * FROM predictions WHERE job_id = ?", (job_id,)
            ).fetchone()
            connection.commit()
        prediction = self._prediction_from_row(prediction_row) if prediction_row else None
        return AnalysisJob(
            id=row["id"],
            video_id=row["video_id"],
            configuration_id=row["configuration_id"],
            prepared_input_id=row["prepared_input_id"],
            state=row["state"],
            start_seconds=row["start_seconds"],
            end_seconds=row["end_seconds"],
            attempt_count=row["attempt_count"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            claim_token=row["claim_token"],
            lease_expires_at=row["lease_expires_at"],
            configuration=self.get_configuration(row["configuration_id"]),
            prediction=prediction,
        )

    def list_jobs(self, limit: int = 20) -> list[AnalysisJob]:
        """List every active job and a bounded number of recent terminal jobs."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        self.recover_expired()
        with self._connect() as connection:
            ids = [
                row["id"]
                for row in connection.execute(
                    """SELECT id FROM jobs WHERE state IN ('queued', 'running')
                    OR id IN (SELECT id FROM jobs
                        WHERE state NOT IN ('queued', 'running')
                        ORDER BY created_at DESC, rowid DESC LIMIT ?)
                    ORDER BY created_at DESC, rowid DESC""",
                    (limit,),
                )
            ]
        return [job for job_id in ids if (job := self.get_job(job_id)) is not None]

    def claim_next_job(self) -> AnalysisJob | None:
        """Atomically claim the oldest queued job for one worker."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired_locked(connection)
            row = connection.execute(
                "SELECT id FROM jobs WHERE state = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            timestamp = self._timestamp()
            token = str(uuid4())
            expires = (self._clock().astimezone(UTC) + timedelta(seconds=90)).isoformat()
            connection.execute(
                """UPDATE jobs SET state = 'running', attempt_count = attempt_count + 1,
                updated_at = ?, claim_token = ?, lease_expires_at = ?
                WHERE id = ? AND state = 'queued'""",
                (timestamp, token, expires, row["id"]),
            )
            connection.commit()
        claimed = self.get_job(row["id"])
        # The lease may have expired between commit and this read. Never hand
        # another worker's newer token to the original claimant.
        if claimed is None or claimed.claim_token != token or claimed.state != "running":
            return None
        return claimed

    def complete_job(
        self,
        job_id: str,
        result: PipelineResult,
        *,
        backend_kind: str,
        model: str,
        fixture_version: str | None,
        claim_token: str,
    ) -> bool:
        """Commit a result only for the current unexpired attempt."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            completed_at = self._timestamp()
            changed = connection.execute(
                """UPDATE jobs SET state = 'succeeded', error = NULL, updated_at = ?,
                claim_token = NULL, lease_expires_at = NULL
                WHERE id = ? AND state = 'running' AND claim_token = ?
                AND lease_expires_at > ?""",
                (completed_at, job_id, claim_token, completed_at),
            ).rowcount
            if changed != 1:
                connection.rollback()
                return False
            connection.execute(
                """INSERT INTO predictions
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
            connection.commit()
            return True

    def fail_job(self, job_id: str, error: str, claim_token: str) -> bool:
        """Persist an explicit processing failure without inventing a label."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self._timestamp()
            changed = connection.execute(
                """UPDATE jobs SET state = 'failed', error = ?, updated_at = ?,
                claim_token = NULL, lease_expires_at = NULL WHERE id = ?
                AND state = 'running' AND claim_token = ? AND lease_expires_at > ?""",
                (error, now, job_id, claim_token, now),
            ).rowcount
            connection.commit()
        return changed == 1

    def renew_claim(self, job_id: str, claim_token: str) -> bool:
        """Extend only the current unexpired worker claim."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self._timestamp()
            expires = (self._clock().astimezone(UTC) + timedelta(seconds=90)).isoformat()
            changed = connection.execute(
                """UPDATE jobs SET lease_expires_at = ?, updated_at = ? WHERE id = ?
                AND state = 'running' AND claim_token = ? AND lease_expires_at > ?""",
                (expires, now, job_id, claim_token, now),
            ).rowcount
            connection.commit()
        return changed == 1

    def _recover_expired_locked(self, connection: sqlite3.Connection) -> int:
        return connection.execute(
            """UPDATE jobs SET state = 'failed', error =
            'Worker interrupted or its lease expired. Retry this run.',
            claim_token = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE state = 'running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
            (self._timestamp(), self._timestamp()),
        ).rowcount

    def recover_expired(self) -> int:
        """Expose abandoned work as retryable failed jobs."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = self._recover_expired_locked(connection)
            connection.commit()
        return changed

    def retry_job(self, job_id: str) -> AnalysisJob:
        """Requeue a failed logical run with its original input and settings."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired_locked(connection)
            changed = connection.execute(
                """UPDATE jobs SET state = 'queued', error = NULL, updated_at = ?
                WHERE id = ? AND state = 'failed'""",
                (self._timestamp(), job_id),
            ).rowcount
            connection.commit()
        if changed != 1:
            raise ValueError("Only a failed job can be retried")
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError("retried job no longer exists")
        return job

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
