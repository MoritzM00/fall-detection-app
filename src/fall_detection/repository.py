import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from fall_detection.models import (
    AnalysisJob,
    GenerationConfiguration,
    RunConfiguration,
    SamplingConfiguration,
    VideoAsset,
)
from fall_detection.prediction import PRESET_ID, THESIS_BASELINE_PROMPT, PipelineResult
from fall_detection.schema import (
    JOB_SELECT,
    configuration_from_row,
    enable_wal,
    job_from_row,
    migrate,
    video_from_row,
)


def utc_now() -> str:
    """Return a stable UTC timestamp for persisted lifecycle events."""
    return datetime.now(UTC).isoformat()


_EXPIRED_SELECT = """SELECT id FROM jobs WHERE state = 'running'
    AND (lease_expires_at IS NULL OR lease_expires_at <= ?)"""


class Repository:
    """SQLite persistence boundary used by API and worker processes."""

    def __init__(self, database_path: Path, clock: Callable[[], datetime] | None = None) -> None:
        """Bind repository operations to one database file."""
        self._database_path = database_path
        self._clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        """Read the injectable clock that every lifecycle age is measured against."""
        return self._clock()

    def timestamp(self) -> str:
        """Return :meth:`now` as the UTC ISO 8601 text stored in lifecycle columns."""
        return self.now().astimezone(UTC).isoformat()

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Run statements in one transaction, committed on exit and rolled back on error.

        ``immediate`` takes the write lock up front (``BEGIN IMMEDIATE``), so a
        read-check-write sequence cannot interleave with another writer. With
        ``immediate=False`` the transaction is deferred: a consistent snapshot for
        reads, or a single write statement, which is atomic on its own.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            connection.commit()

    @contextmanager
    def _connect(self, *, foreign_keys: bool = True) -> Iterator[sqlite3.Connection]:
        """Open a connection with the pragmas SQLite does not persist in the file.

        ``timeout`` sets the busy timeout. WAL mode is persistent and is set once
        by :meth:`initialize`, so ordinary connections never touch the journal mode.
        """
        connection = sqlite3.connect(self._database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Enable WAL, then create and migrate the schema under a database-wide write lock."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(foreign_keys=False) as connection:
            enable_wal(connection)
            connection.execute("BEGIN IMMEDIATE")
            migrate(connection, self.timestamp())
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
        return video_from_row(row)

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
        return video_from_row(row)

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
        return video_from_row(row)

    def get_video(self, video_id: str) -> VideoAsset | None:
        """Read one asset without exposing its machine-local path."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return video_from_row(row) if row is not None else None

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
        with self.transaction() as connection:
            video = connection.execute("SELECT id FROM videos WHERE id = ?", (video_id,)).fetchone()
            if video is None:
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
        return configuration_from_row(row)

    def get_job(self, job_id: str) -> AnalysisJob | None:
        """Read a job with its configuration and persisted result; never writes."""
        with self._connect() as connection:
            row = connection.execute(f"{JOB_SELECT} WHERE j.id = ?", (job_id,)).fetchone()
        return job_from_row(row) if row is not None else None

    def list_jobs(self, limit: int = 20) -> list[AnalysisJob]:
        """List every active job and a bounded number of recent terminal jobs.

        One connection and one read statement, independent of the number of jobs.
        Expired leases are recovered by the worker and the API's recovery timer.
        """
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connect() as connection:
            rows = connection.execute(
                f"""{JOB_SELECT} WHERE j.state IN ('queued', 'running')
                OR j.id IN (SELECT id FROM jobs
                    WHERE state NOT IN ('queued', 'running')
                    ORDER BY created_at DESC, rowid DESC LIMIT ?)
                ORDER BY j.created_at DESC, j.rowid DESC""",
                (limit,),
            ).fetchall()
        return [job_from_row(row) for row in rows]

    def claim_next_job(self) -> AnalysisJob | None:
        """Atomically claim the oldest queued job for one worker."""
        with self.transaction() as connection:
            self._recover_expired_locked(connection)
            row = connection.execute(
                """SELECT id FROM jobs WHERE state = 'queued' ORDER BY
                CASE WHEN id IN (SELECT job_id FROM monitoring_windows) THEN 1 ELSE 0 END,
                created_at, rowid LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            timestamp = self.timestamp()
            token = str(uuid4())
            expires = (self.now().astimezone(UTC) + timedelta(seconds=90)).isoformat()
            connection.execute(
                """UPDATE jobs SET state = 'running', attempt_count = attempt_count + 1,
                updated_at = ?, claim_token = ?, lease_expires_at = ?
                WHERE id = ? AND state = 'queued'""",
                (timestamp, token, expires, row["id"]),
            )
            self._attempt_event(connection, row["id"], "started", timestamp)
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
        with self.transaction() as connection:
            completed_at = self.timestamp()
            changed = connection.execute(
                """UPDATE jobs SET state = 'succeeded', error = NULL, updated_at = ?,
                claim_token = NULL, lease_expires_at = NULL
                WHERE id = ? AND state = 'running' AND claim_token = ?
                AND lease_expires_at > ?""",
                (completed_at, job_id, claim_token, completed_at),
            ).rowcount
            if changed != 1:
                return False
            connection.execute(
                """INSERT INTO predictions
                (id, job_id, label, raw_response, sampled_timestamps_json, backend_kind,
                 model, fixture_version, request_duration_ms, total_duration_ms, completed_at,
                 prompt_tokens, completion_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    result.prompt_tokens,
                    result.completion_tokens,
                ),
            )
            self._attempt_event(connection, job_id, "succeeded", completed_at)
            return True

    def fail_job(self, job_id: str, error: str, claim_token: str) -> bool:
        """Persist an explicit processing failure without inventing a label."""
        with self.transaction() as connection:
            now = self.timestamp()
            changed = connection.execute(
                """UPDATE jobs SET state = 'failed', error = ?, updated_at = ?,
                claim_token = NULL, lease_expires_at = NULL WHERE id = ?
                AND state = 'running' AND claim_token = ? AND lease_expires_at > ?""",
                (error, now, job_id, claim_token, now),
            ).rowcount
            if changed:
                self._attempt_event(connection, job_id, "failed", now, error)
        return changed == 1

    def renew_claim(self, job_id: str, claim_token: str) -> bool:
        """Extend only the current unexpired worker claim."""
        with self.transaction() as connection:
            now = self.timestamp()
            expires = (self.now().astimezone(UTC) + timedelta(seconds=90)).isoformat()
            changed = connection.execute(
                """UPDATE jobs SET lease_expires_at = ?, updated_at = ? WHERE id = ?
                AND state = 'running' AND claim_token = ? AND lease_expires_at > ?""",
                (expires, now, job_id, claim_token, now),
            ).rowcount
        return changed == 1

    def _recover_expired_locked(self, connection: sqlite3.Connection) -> int:
        now = self.timestamp()
        expired = connection.execute(_EXPIRED_SELECT, (now,)).fetchall()
        for row in expired:
            self._attempt_event(connection, row["id"], "lease_expired", now)
        return connection.execute(
            """UPDATE jobs SET state = 'failed', error =
            'Worker interrupted or its lease expired. Retry this run.',
            claim_token = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE state = 'running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
            (now, now),
        ).rowcount

    def recover_expired(self) -> int:
        """Expose abandoned work as retryable failed jobs.

        Runs in the worker loop and on API startup and timer, never on reads. The
        write lock is taken only when a read finds an expired lease.
        """
        with self._connect() as connection:
            if connection.execute(_EXPIRED_SELECT, (self.timestamp(),)).fetchone() is None:
                return 0
            connection.execute("BEGIN IMMEDIATE")
            changed = self._recover_expired_locked(connection)
            connection.commit()
        return changed

    def retry_job(self, job_id: str) -> AnalysisJob:
        """Requeue a failed logical run with its original input and settings."""
        with self.transaction() as connection:
            self._recover_expired_locked(connection)
            if connection.execute(
                "SELECT 1 FROM monitoring_windows WHERE job_id=?", (job_id,)
            ).fetchone():
                raise ValueError(
                    "Monitoring coverage is immutable; restart or seek to schedule a new generation"
                )
            changed = connection.execute(
                """UPDATE jobs SET state = 'queued', error = NULL, updated_at = ?
                WHERE id = ? AND state = 'failed'""",
                (self.timestamp(), job_id),
            ).rowcount
            if changed:
                self._attempt_event(connection, job_id, "retry_requested", self.timestamp())
        if changed != 1:
            raise ValueError("Only a failed job can be retried")
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError("retried job no longer exists")
        return job

    @staticmethod
    def _attempt_event(
        connection: sqlite3.Connection,
        job_id: str,
        event: str,
        timestamp: str,
        error: str | None = None,
    ) -> None:
        """Record forward-only audit events without copying sensitive diagnostics."""
        connection.execute(
            """INSERT INTO attempt_events(job_id,attempt,event,occurred_at,error_sha256)
            SELECT id,attempt_count,?,?,? FROM jobs WHERE id=?""",
            (
                event,
                timestamp,
                hashlib.sha256(error.encode()).hexdigest() if error else None,
                job_id,
            ),
        )
