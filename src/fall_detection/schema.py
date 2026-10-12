"""SQLite schema creation, in-place migrations and row decoding for the shared database."""

import json
import sqlite3
from time import monotonic, sleep

from fall_detection.models import (
    AnalysisJob,
    GenerationConfiguration,
    PredictionResult,
    RunConfiguration,
    SamplingConfiguration,
    VideoAsset,
)

SCHEMA_VERSION = 6


def enable_wal(connection: sqlite3.Connection) -> None:
    """Switch the database file to WAL; the mode persists for later connections."""
    # Changing journal mode can return SQLITE_BUSY immediately, even with
    # busy_timeout configured, when another startup connection holds a lock.
    deadline = monotonic() + 10
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if (
                getattr(exc, "sqlite_errorcode", 0) & 0xFF != sqlite3.SQLITE_BUSY
                or monotonic() >= deadline
            ):
                raise
            sleep(0.01)


def migrate(connection: sqlite3.Connection, timestamp: str) -> None:
    """Create and migrate all tables inside the caller's write transaction."""
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('upload', 'synthetic', 'dataset', 'live')),
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
            completed_at TEXT NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER
        )""",
    ):
        connection.execute(statement)
    connection.execute("""CREATE TABLE IF NOT EXISTS attempt_events (
        id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
        attempt INTEGER NOT NULL, event TEXT NOT NULL, occurred_at TEXT NOT NULL,
        error_sha256 TEXT)""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS attempt_events_by_job ON attempt_events(job_id,id)"
    )
    videos_schema = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'videos'"
    ).fetchone()["sql"]
    if "'live'" not in videos_schema:
        # SQLite cannot alter a CHECK constraint. Preserve the old rows
        # while moving the table name under the same transaction.
        connection.execute("""CREATE TABLE videos_new (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                source TEXT NOT NULL
                    CHECK (source IN ('upload', 'synthetic', 'dataset', 'live')),
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
    prediction_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(predictions)")
    }
    for column in ("prompt_tokens", "completion_tokens"):
        if column not in prediction_columns:
            connection.execute(f"ALTER TABLE predictions ADD COLUMN {column} INTEGER")
    connection.execute(
        """UPDATE jobs SET state = 'failed', error =
        'Worker interrupted before claim ownership was recorded. Retry this run.',
        updated_at = ? WHERE state = 'running' AND claim_token IS NULL""",
        (timestamp,),
    )
    _migrate_monitoring(connection)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


MONITORING_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS monitoring_commands (
        session_id TEXT NOT NULL REFERENCES monitoring_sessions(id), command_id TEXT NOT NULL,
        payload TEXT NOT NULL, PRIMARY KEY(session_id, command_id))""",
    """CREATE TABLE IF NOT EXISTS monitoring_sessions (
        id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL DEFAULT 1,
        video_id TEXT NOT NULL REFERENCES videos(id), state TEXT NOT NULL,
        generation INTEGER NOT NULL, segment_id TEXT NOT NULL,
        position REAL NOT NULL, duration REAL NOT NULL, duration_verified INTEGER NOT NULL DEFAULT 0, origin REAL NOT NULL,
        next_sequence INTEGER NOT NULL, stride REAL NOT NULL, expiration REAL NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, recovery_reason TEXT)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_running_session
        ON monitoring_sessions(state) WHERE state = 'running'""",
    """CREATE TABLE IF NOT EXISTS monitoring_segments (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES monitoring_sessions(id),
        generation INTEGER NOT NULL, start_seconds REAL NOT NULL,
        configuration_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS monitoring_windows (
        id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL DEFAULT 1, session_id TEXT NOT NULL REFERENCES monitoring_sessions(id),
        generation INTEGER NOT NULL, sequence INTEGER NOT NULL, sequence_end INTEGER NOT NULL,
        segment_id TEXT NOT NULL REFERENCES monitoring_segments(id),
        start_seconds REAL NOT NULL, end_seconds REAL NOT NULL, available_seconds REAL NOT NULL,
        state TEXT NOT NULL, reason TEXT, prepared_input_id TEXT, job_id TEXT REFERENCES jobs(id),
        created_at TEXT NOT NULL, UNIQUE(session_id, generation, sequence))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_pending_window
        ON monitoring_windows(session_id) WHERE state IN ('pending', 'preparing')""",
    """CREATE TABLE IF NOT EXISTS live_runs (
        video_id TEXT NOT NULL REFERENCES videos(id), run_id TEXT NOT NULL,
        offset_seconds REAL NOT NULL, created_at TEXT NOT NULL, run_order INTEGER,
        PRIMARY KEY(video_id, run_id))""",
    """CREATE TABLE IF NOT EXISTS live_frames (
        video_id TEXT NOT NULL REFERENCES videos(id), seq INTEGER NOT NULL,
        run_id TEXT NOT NULL, run_seq INTEGER NOT NULL,
        capture_seconds REAL NOT NULL, sha256 TEXT NOT NULL, received_at TEXT NOT NULL,
        PRIMARY KEY(video_id, seq), UNIQUE(video_id, run_id, run_seq))""",
)


def _migrate_monitoring(connection: sqlite3.Connection) -> None:
    """Add replay tables without rewriting clip jobs or saved configurations."""
    for statement in MONITORING_SCHEMA:
        connection.execute(statement)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(monitoring_sessions)")}
    # Live sessions: capture policy and the newest ingest time; recordings keep the defaults.
    if "source_kind" not in columns:
        connection.execute(
            "ALTER TABLE monitoring_sessions ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'recording'"
        )
    if "capture_fps" not in columns:
        connection.execute("ALTER TABLE monitoring_sessions ADD COLUMN capture_fps REAL")
    if "last_frame_at" not in columns:
        connection.execute("ALTER TABLE monitoring_sessions ADD COLUMN last_frame_at TEXT")
    # Client-chosen run ordinal; NULL for runs stored before clients sent one.
    run_columns = {row[1] for row in connection.execute("PRAGMA table_info(live_runs)")}
    if "run_order" not in run_columns:
        connection.execute("ALTER TABLE live_runs ADD COLUMN run_order INTEGER")


# Row decoding lives beside the table layout: adding a column changes both together.
# One read statement returns a job with its configuration snapshot and result.
JOB_SELECT = """SELECT j.*, c.model AS c_model, c.prompt_preset AS c_prompt_preset,
    c.prompt_text AS c_prompt_text, c.preprocessing_json AS c_preprocessing_json,
    c.generation_json AS c_generation_json, c.backend_kind AS c_backend_kind,
    c.fixture_version AS c_fixture_version, p.id AS p_id, p.label AS p_label,
    p.raw_response AS p_raw_response, p.sampled_timestamps_json AS p_sampled_timestamps_json,
    p.backend_kind AS p_backend_kind, p.model AS p_model, p.fixture_version AS p_fixture_version,
    p.request_duration_ms AS p_request_duration_ms, p.total_duration_ms AS p_total_duration_ms,
    p.completed_at AS p_completed_at, p.prompt_tokens AS p_prompt_tokens,
    p.completion_tokens AS p_completion_tokens
    FROM jobs j LEFT JOIN configurations c ON c.id = j.configuration_id
    LEFT JOIN predictions p ON p.job_id = j.id"""


def video_from_row(row: sqlite3.Row) -> VideoAsset:
    """Decode one ``videos`` row."""
    return VideoAsset(
        id=row["id"],
        filename=row["filename"],
        source=row["source"],
        storage_key=row["storage_key"],
        duration_seconds=row["duration_seconds"],
        created_at=row["created_at"],
    )


def job_from_row(row: sqlite3.Row) -> AnalysisJob:
    """Decode one ``JOB_SELECT`` row with its configuration snapshot and result."""
    if row["c_model"] is None:
        raise ValueError("job configuration no longer exists")
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
        configuration=configuration_from_row(row, "c_"),
        prediction=prediction_from_row(row, "p_") if row["p_id"] is not None else None,
    )


def configuration_from_row(row: sqlite3.Row, prefix: str = "") -> RunConfiguration:
    """Decode a saved run configuration, optionally from prefixed join columns."""
    preprocessing = json.loads(row[f"{prefix}preprocessing_json"])
    # Historic snapshots did not contain FPS. Their backend is deliberately unknown.
    preprocessing.setdefault("fps", 7.5)
    return RunConfiguration(
        model=row[f"{prefix}model"],
        prompt_preset=row[f"{prefix}prompt_preset"],
        prompt_text=row[f"{prefix}prompt_text"],
        preprocessing=SamplingConfiguration.model_validate(preprocessing),
        generation=GenerationConfiguration.model_validate_json(row[f"{prefix}generation_json"]),
        backend_kind=row[f"{prefix}backend_kind"],
        fixture_version=row[f"{prefix}fixture_version"],
    )


def prediction_from_row(row: sqlite3.Row, prefix: str = "") -> PredictionResult:
    """Decode a persisted prediction, optionally from prefixed join columns."""
    return PredictionResult(
        id=row[f"{prefix}id"],
        label=row[f"{prefix}label"],
        raw_response=row[f"{prefix}raw_response"],
        sampled_timestamps=json.loads(row[f"{prefix}sampled_timestamps_json"]),
        backend_kind=row[f"{prefix}backend_kind"],
        model=row[f"{prefix}model"],
        fixture_version=row[f"{prefix}fixture_version"],
        request_duration_ms=row[f"{prefix}request_duration_ms"],
        total_duration_ms=row[f"{prefix}total_duration_ms"],
        completed_at=row[f"{prefix}completed_at"],
        prompt_tokens=row[f"{prefix}prompt_tokens"],
        completion_tokens=row[f"{prefix}completion_tokens"],
    )
