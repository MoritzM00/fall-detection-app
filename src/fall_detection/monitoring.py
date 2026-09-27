"""Proposed recorded replay behavior; independent of research inference policy."""

import json
import math
import sqlite3
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from fall_detection.models import GenerationConfiguration, RunConfiguration, SamplingConfiguration
from fall_detection.prompts import PRESET_ID, THESIS_BASELINE_PROMPT
from fall_detection.repository import Repository


class SessionConfiguration(BaseModel):
    """Editable inference settings; replay policy is fixed at session creation."""

    video_id: str
    frame_count: int = Field(default=16, ge=2, le=32)
    fps: float = Field(default=7.5, gt=0, le=30, allow_inf_nan=False)
    size: int = Field(default=448, ge=224, le=672)
    model: str | None = Field(default=None, min_length=1)
    prompt_text: str | None = Field(default=None, min_length=1, max_length=16000)
    generation: GenerationConfiguration | None = None

    @field_validator("prompt_text")
    @classmethod
    def validate_prompt(cls, value):
        """Reject blank custom prompts."""
        if value is not None and not value.strip():
            raise ValueError("Prompt must contain text")
        return value

    @model_validator(mode="after")
    def validate_sampling(self):
        """Bound preparation work and answer token budgets."""
        if (self.frame_count - 1) / self.fps > 30:
            raise ValueError("Monitoring windows are limited to 30 seconds")
        if self.generation is not None and self.generation.max_tokens < 16:
            raise ValueError("Monitoring requires at least 16 output tokens")
        return self


class SessionCreate(SessionConfiguration):
    """Replay source, playback bound and persisted scheduling policy."""

    start_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    stride_seconds: float = Field(default=2, gt=0, le=30, allow_inf_nan=False)
    expiration_seconds: float = Field(default=5, gt=0, le=300, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_replay(self):
        """Bound decode work and reject ambiguous caller-prepared bundles."""
        if (self.frame_count - 1) / self.fps > 30:
            raise ValueError("Monitoring windows are limited to 30 seconds")
        if self.start_seconds >= self.duration_seconds:
            raise ValueError("Initial position must precede the playback bound")
        return self


class SessionCommand(BaseModel):
    """Explicit authoritative playback position and lifecycle command."""

    action: Literal["start", "pause", "resume", "stop", "seek", "restart", "position", "configure"]
    position_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    configuration: SessionConfiguration | None = None
    command_id: str | None = Field(default=None, min_length=1, max_length=128)


SCHEMA = (
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
)


def migrate(connection: sqlite3.Connection) -> None:
    """Add replay tables without rewriting clip jobs or saved configurations."""
    for statement in SCHEMA:
        connection.execute(statement)


class Monitoring:
    """Transactional replay commands and bounded scheduling on the existing database."""

    def __init__(self, repository: Repository) -> None:
        """Share the repository's injectable clock and transaction boundary."""
        self.repository = repository

    @staticmethod
    def configuration(
        request: SessionConfiguration,
        model: str,
        backend: str,
        fixture: str | None,
    ):
        """Snapshot validated serving identity before scheduling any input."""
        if backend not in {"mock", "vllm"}:
            raise ValueError("Unsupported monitoring backend")
        serving_backend: Literal["mock", "vllm"] = "mock" if backend == "mock" else "vllm"
        prompt = request.prompt_text or THESIS_BASELINE_PROMPT
        return RunConfiguration(
            model=model,
            backend_kind=serving_backend,
            fixture_version=fixture,
            prompt_preset=PRESET_ID if prompt == THESIS_BASELINE_PROMPT else "custom",
            prompt_text=prompt,
            preprocessing=SamplingConfiguration(
                frames=request.frame_count, fps=request.fps, resize=request.size
            ),
            generation=request.generation or GenerationConfiguration(temperature=0, max_tokens=32),
        )

    def create(self, request: SessionCreate, configuration: RunConfiguration) -> dict:
        """Create a paused session and its immutable initial configuration segment."""
        session_id, segment = str(uuid4()), str(uuid4())
        timestamp = self.repository._timestamp()
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute(
                    "SELECT id FROM videos WHERE id=?", (request.video_id,)
                ).fetchone()
                is None
            ):
                raise KeyError(request.video_id)
            video = self.repository.get_video(request.video_id)
            if (
                video is not None
                and video.duration_seconds is not None
                and request.duration_seconds > video.duration_seconds
            ):
                raise ValueError("Playback bound exceeds known source duration")
            connection.execute(
                """INSERT INTO monitoring_sessions
                (id,video_id,state,generation,segment_id,position,duration,origin,next_sequence,
                 stride,expiration,created_at,updated_at)
                VALUES (?,?,'paused',0,?,?,?, ?,0,?,?,?,?)""",
                (
                    session_id,
                    request.video_id,
                    segment,
                    request.start_seconds,
                    request.duration_seconds,
                    request.start_seconds,
                    request.stride_seconds,
                    request.expiration_seconds,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "INSERT INTO monitoring_segments VALUES (?,?,?,?,?,?)",
                (
                    segment,
                    session_id,
                    0,
                    request.start_seconds,
                    configuration.model_dump_json(),
                    timestamp,
                ),
            )
            if video is not None and video.source == "synthetic":
                connection.execute(
                    "UPDATE monitoring_sessions SET duration_verified=1 WHERE id=?", (session_id,)
                )
            connection.commit()
        return self.get(session_id)

    def get(self, session_id: str) -> dict:
        """Read lifecycle and the newest eligible result; preserve all history separately."""
        with self.repository._connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT * FROM monitoring_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            result = dict(row)
            latest = connection.execute(
                """SELECT w.job_id FROM monitoring_windows w
                JOIN jobs j ON j.id=w.job_id WHERE w.session_id=? AND w.generation=?
                AND w.segment_id=? AND j.state='succeeded' ORDER BY w.sequence DESC LIMIT 1""",
                (session_id, row["generation"], row["segment_id"]),
            ).fetchone()
            segment = connection.execute(
                "SELECT * FROM monitoring_segments WHERE id=?", (row["segment_id"],)
            ).fetchone()
            result["configuration"] = json.loads(segment["configuration_json"])
            result["latest_job_id"] = latest["job_id"] if latest else None
            connection.commit()
        return result

    def history(self, session_id: str, limit: int = 100, before: int | None = None) -> list[dict]:
        """Page immutable intervals, skipped coverage and input/job references."""
        self.get(session_id)
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self.repository._connect() as connection:
            rows = connection.execute(
                """SELECT rowid AS cursor,* FROM monitoring_windows
                WHERE session_id=? AND (? IS NULL OR rowid < ?) ORDER BY rowid DESC LIMIT ?""",
                (session_id, before, before, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recover(self) -> None:
        """Pause replay on process startup; never silently resume a playback timeline."""
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE monitoring_sessions SET state='paused',
                recovery_reason='process_restart',updated_at=? WHERE state='running'""",
                (self.repository._timestamp(),),
            )
            connection.execute("""UPDATE monitoring_windows SET state='skipped',reason='process_restart'
                WHERE state IN ('pending','preparing')""")
            connection.execute(
                "UPDATE monitoring_windows SET state='skipped',reason='process_restart' WHERE job_id IN (SELECT id FROM jobs WHERE state='queued')"
            )
            connection.execute(
                "UPDATE jobs SET state='cancelled',error='process_restart',updated_at=? WHERE state='queued' AND id IN (SELECT job_id FROM monitoring_windows)",
                (self.repository._timestamp(),),
            )
            connection.commit()

    def command(
        self,
        session_id: str,
        command: SessionCommand,
        configuration: RunConfiguration | None = None,
    ) -> dict:
        """Apply timeline-preserving pause/resume or fence a changed playback generation."""
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM monitoring_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            if command.command_id is not None:
                previous = connection.execute(
                    "SELECT payload FROM monitoring_commands WHERE session_id=? AND command_id=?",
                    (session_id, command.command_id),
                ).fetchone()
                if previous:
                    if previous[0] != command.model_dump_json():
                        raise ValueError("Command identity was already used with another payload")
                    connection.commit()
                    return self.get(session_id)
                connection.execute(
                    "INSERT INTO monitoring_commands VALUES (?,?,?)",
                    (session_id, command.command_id, command.model_dump_json()),
                )
            values = dict(row)
            action = command.action
            if command.position_seconds is not None:
                if command.position_seconds > row["duration"]:
                    raise ValueError("Position exceeds playback bound")
                if action not in {"seek", "restart"} and command.position_seconds < row["position"]:
                    raise ValueError("Backward playback requires seek")
                values["position"] = command.position_seconds
            if action == "seek" and command.position_seconds is None:
                raise ValueError("Seek requires an explicit position")
            if action in {"start", "resume"}:
                if row["state"] == "stopped":
                    raise ValueError("Stopped sessions require restart")
                values["state"] = "running"
            elif action in {"pause", "stop"}:
                values["state"] = "paused" if action == "pause" else "stopped"
            elif action == "restart":
                values["state"] = "running"
                values["position"] = command.position_seconds or 0
            elif action == "position" and command.position_seconds is None:
                raise ValueError("Position command requires an explicit position")
            if action in {"stop", "seek", "restart", "configure"}:
                connection.execute(
                    """UPDATE monitoring_windows SET state='skipped',reason=?
                    WHERE session_id=? AND state IN ('pending','preparing')""",
                    (action, session_id),
                )
                connection.execute(
                    "UPDATE monitoring_windows SET state='skipped',reason=? WHERE session_id=? AND job_id IN (SELECT id FROM jobs WHERE state='queued')",
                    (action, session_id),
                )
                connection.execute(
                    """UPDATE jobs SET state='cancelled',error=?,updated_at=?
                    WHERE state='queued' AND id IN
                    (SELECT job_id FROM monitoring_windows WHERE session_id=?)""",
                    (action, self.repository._timestamp(), session_id),
                )
                if action != "configure":
                    values["generation"] += 1
                values["origin"] = values["position"]
                values["next_sequence"] = 0 if action != "configure" else values["next_sequence"]
                # Configuration segments start at the first unscheduled sequence.
                if action == "configure":
                    values["origin"] -= values["next_sequence"] * values["stride"]
                segment = str(uuid4())
                if action == "configure":
                    if configuration is None or command.configuration is None:
                        raise ValueError("Configure requires a configuration")
                    if command.configuration.video_id != row["video_id"]:
                        raise ValueError("Configuration cannot change the source")
                else:
                    existing = connection.execute(
                        "SELECT configuration_json FROM monitoring_segments WHERE id=?",
                        (row["segment_id"],),
                    ).fetchone()
                    configuration = RunConfiguration.model_validate_json(existing[0])
                connection.execute(
                    "INSERT INTO monitoring_segments VALUES (?,?,?,?,?,?)",
                    (
                        segment,
                        session_id,
                        values["generation"],
                        values["position"],
                        configuration.model_dump_json(),
                        self.repository._timestamp(),
                    ),
                )
                values["segment_id"] = segment
            # Pause drops only the not-yet-prepared candidate; resume uses the same cursor.
            if action == "pause":
                connection.execute(
                    "UPDATE monitoring_windows SET state='skipped',reason='pause' WHERE session_id=? AND state='pending'",
                    (session_id,),
                )
            connection.execute(
                """UPDATE monitoring_sessions SET state=?,generation=?,segment_id=?,position=?,
                origin=?,next_sequence=?,updated_at=?,recovery_reason=NULL WHERE id=?""",
                (
                    values["state"],
                    values["generation"],
                    values["segment_id"],
                    values["position"],
                    values["origin"],
                    values["next_sequence"],
                    self.repository._timestamp(),
                    session_id,
                ),
            )
            connection.commit()
        return self.get(session_id)

    def tick(self) -> None:
        """Admit only completed playback intervals, coalescing backlog into explicit coverage."""
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            queued = connection.execute("""SELECT w.*,s.position,s.expiration FROM monitoring_windows w
                JOIN monitoring_sessions s ON s.id=w.session_id JOIN jobs j ON j.id=w.job_id
                WHERE j.state='queued'""").fetchall()
            for candidate in queued:
                age = (
                    self.repository._clock() - datetime.fromisoformat(candidate["created_at"])
                ).total_seconds()
                if (
                    age > candidate["expiration"]
                    or candidate["position"] - candidate["end_seconds"] > candidate["expiration"]
                ):
                    connection.execute(
                        "UPDATE jobs SET state='skipped',error='Monitoring window expired before claim',updated_at=? WHERE id=? AND state='queued'",
                        (self.repository._timestamp(), candidate["job_id"]),
                    )
                    connection.execute(
                        "UPDATE monitoring_windows SET state='skipped',reason='expired' WHERE id=?",
                        (candidate["id"],),
                    )
            row = connection.execute(
                "SELECT * FROM monitoring_sessions WHERE state='running' AND duration_verified=1"
            ).fetchone()
            if row is None:
                connection.commit()
                return
            config = RunConfiguration.model_validate_json(
                connection.execute(
                    "SELECT configuration_json FROM monitoring_segments WHERE id=?",
                    (row["segment_id"],),
                ).fetchone()[0]
            )
            width = (config.preprocessing.frames - 1) / config.preprocessing.fps
            seq = row["next_sequence"]
            last = math.floor((row["position"] - row["origin"] - width + 1e-9) / row["stride"])
            pending = connection.execute(
                "SELECT * FROM monitoring_windows WHERE session_id=? AND state IN ('pending','preparing')",
                (row["id"],),
            ).fetchone()
            if pending is not None and pending["state"] == "preparing":
                if (
                    self.repository._clock() - datetime.fromisoformat(pending["created_at"])
                ).total_seconds() > 90:
                    connection.execute(
                        "UPDATE monitoring_windows SET state='failed',reason='preparation_interrupted' WHERE id=?",
                        (pending["id"],),
                    )
                    connection.execute(
                        "UPDATE monitoring_sessions SET state='paused',recovery_reason='preparation_interrupted' WHERE id=?",
                        (row["id"],),
                    )
                connection.commit()
                return
            if pending is not None and (
                pending["sequence"] < last
                or row["position"] - pending["end_seconds"] > row["expiration"]
                or (
                    self.repository._clock() - datetime.fromisoformat(pending["created_at"])
                ).total_seconds()
                > row["expiration"]
            ):
                connection.execute(
                    "UPDATE monitoring_windows SET state='skipped',reason='expired' WHERE id=?",
                    (pending["id"],),
                )
                pending = None
            if seq <= last:
                if seq < last:
                    self._window(connection, row, seq, last - 1, width, "skipped", "superseded")
                end = row["origin"] + last * row["stride"] + width
                state = "skipped" if row["position"] - end > row["expiration"] else "pending"
                self._window(
                    connection,
                    row,
                    last,
                    last,
                    width,
                    state,
                    "expired" if state == "skipped" else None,
                )
                seq = last + 1
            start = row["origin"] + seq * row["stride"]
            if (
                row["position"] >= row["duration"]
                and start < row["duration"]
                and start + width > row["duration"]
            ):
                self._window(
                    connection,
                    row,
                    seq,
                    seq,
                    width,
                    "skipped",
                    "incomplete_tail",
                    end=row["duration"],
                )
                seq += 1
            connection.execute(
                "UPDATE monitoring_sessions SET next_sequence=? WHERE id=?", (seq, row["id"])
            )
            connection.commit()

    def _window(self, connection, row, seq, last, width, state, reason, *, end=None):
        connection.execute(
            """INSERT INTO monitoring_windows
            (id,session_id,generation,sequence,sequence_end,segment_id,start_seconds,end_seconds,
             available_seconds,state,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid4()),
                row["id"],
                row["generation"],
                seq,
                last,
                row["segment_id"],
                row["origin"] + seq * row["stride"],
                end if end is not None else row["origin"] + last * row["stride"] + width,
                row["position"],
                state,
                reason,
                self.repository._timestamp(),
            ),
        )

    def verify_source(self, settings) -> None:
        """Read source playback bounds on the worker before any real-window admission."""
        import av

        from fall_detection.storage_lock import storage_lock

        with self.repository._connect() as connection:
            row = connection.execute(
                "SELECT * FROM monitoring_sessions WHERE state='running' AND duration_verified=0"
            ).fetchone()
        if row is None:
            return
        try:
            video = self.repository.get_video(row["video_id"])
            if video is None or video.storage_key is None:
                raise ValueError("Stored replay source is missing")
            with storage_lock(settings.data_dir, exclusive=False):
                path = (settings.data_dir / video.storage_key).resolve()
                if not path.is_relative_to(settings.data_dir.resolve()):
                    raise ValueError("Replay source path is unsafe")
                with av.open(str(path)) as container:
                    stream = next(
                        (item for item in container.streams if item.type == "video"), None
                    )
                    if stream is None or stream.duration is None or stream.time_base is None:
                        raise ValueError("Replay requires a video stream with a duration")
                    duration = min(row["duration"], float(stream.duration * stream.time_base))
                    if duration <= row["origin"]:
                        raise ValueError("Replay starts beyond the source duration")
                with self.repository._connect() as connection:
                    connection.execute(
                        "UPDATE monitoring_sessions SET duration=?,position=MIN(position,?),duration_verified=1 WHERE id=?",
                        (duration, duration, row["id"]),
                    )
        except (ValueError, OSError, av.error.FFmpegError) as exc:
            with self.repository._connect() as connection:
                connection.execute(
                    "UPDATE monitoring_sessions SET state='paused',recovery_reason=? WHERE id=?",
                    (str(exc), row["id"]),
                )

    def prepare_next(self, settings) -> bool:
        """Prepare one candidate on the worker, yielding to all queued clip jobs."""
        import av

        from fall_detection.models import PreparationRequest
        from fall_detection.preparation import prepare_video
        from fall_detection.storage_lock import storage_lock

        self.verify_source(settings)
        self.tick()
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Global monitoring work bound includes obsolete generations still running.
            if (
                connection.execute("""SELECT 1 FROM jobs WHERE state IN ('queued','running')
                AND id IN (SELECT job_id FROM monitoring_windows)""").fetchone()
                or connection.execute(
                    """SELECT 1 FROM jobs WHERE state='queued' AND id NOT IN
                (SELECT job_id FROM monitoring_windows WHERE job_id IS NOT NULL)"""
                ).fetchone()
            ):
                connection.commit()
                return False
            row = connection.execute("""SELECT w.*,s.video_id FROM monitoring_windows w
                JOIN monitoring_sessions s ON s.id=w.session_id WHERE w.state='pending'
                AND s.state='running' AND s.generation=w.generation AND s.segment_id=w.segment_id""").fetchone()
            if row is None:
                connection.commit()
                return False
            connection.execute(
                "UPDATE monitoring_windows SET state='preparing' WHERE id=?", (row["id"],)
            )
            config = RunConfiguration.model_validate_json(
                connection.execute(
                    "SELECT configuration_json FROM monitoring_segments WHERE id=?",
                    (row["segment_id"],),
                ).fetchone()[0]
            )
            connection.commit()
        prepared = None
        try:
            video = self.repository.get_video(row["video_id"])
            if video is None:
                raise ValueError("Session source is missing")
            # Lock spans publication and durable reference, preventing prune races.
            with storage_lock(settings.data_dir, exclusive=False):
                if video.source != "synthetic":
                    prepared = prepare_video(
                        video,
                        PreparationRequest(
                            video_id=video.id,
                            start_seconds=row["start_seconds"],
                            frame_count=config.preprocessing.frames,
                            fps=config.preprocessing.fps,
                            size=config.preprocessing.resize,
                        ),
                        settings.data_dir,
                        settings.inspection_pngs,
                        causal=True,
                    )
                elif config.backend_kind != "mock":
                    raise ValueError("Synthetic replay requires mock serving")
                with self.repository._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    current = connection.execute(
                        "SELECT * FROM monitoring_sessions WHERE id=?", (row["session_id"],)
                    ).fetchone()
                    window = connection.execute(
                        "SELECT state FROM monitoring_windows WHERE id=?", (row["id"],)
                    ).fetchone()
                    eligible = (
                        current["state"] == "running"
                        and current["generation"] == row["generation"]
                        and current["segment_id"] == row["segment_id"]
                        and window["state"] == "preparing"
                    )
                    if (
                        self.repository._clock() - datetime.fromisoformat(row["created_at"])
                    ).total_seconds() > current["expiration"] or current["position"] - row[
                        "end_seconds"
                    ] > current["expiration"]:
                        connection.execute(
                            "UPDATE monitoring_windows SET state='skipped',reason='expired' WHERE id=? AND state='preparing'",
                            (row["id"],),
                        )
                        connection.commit()
                        return True
                    if not eligible:
                        connection.execute(
                            "UPDATE monitoring_windows SET state='skipped',reason=COALESCE(reason,'lifecycle_changed') WHERE id=? AND state='preparing'",
                            (row["id"],),
                        )
                        connection.commit()
                        return True
                    if prepared:
                        config = config.model_copy(
                            update={
                                "preprocessing": config.preprocessing.model_copy(
                                    update={
                                        "version": prepared.preprocessing_version,
                                        "bundle_sha256": prepared.bundle_sha256,
                                    }
                                )
                            }
                        )
                    job_id, configuration_id = str(uuid4()), str(uuid4())
                    timestamp = self.repository._timestamp()
                    connection.execute(
                        """INSERT INTO configurations
                        (id,schema_version,model,prompt_preset,prompt_text,preprocessing_json,
                         generation_json,created_at,backend_kind,fixture_version) VALUES (?,2,?,?,?,?,?,?,?,?)""",
                        (
                            configuration_id,
                            config.model,
                            config.prompt_preset,
                            config.prompt_text,
                            config.preprocessing.model_dump_json(),
                            config.generation.model_dump_json(),
                            timestamp,
                            config.backend_kind,
                            config.fixture_version,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO jobs
                        (id,video_id,configuration_id,prepared_input_id,state,start_seconds,end_seconds,
                         created_at,updated_at) VALUES (?,?,?,?,'queued',?,?,?,?)""",
                        (
                            job_id,
                            video.id,
                            configuration_id,
                            prepared.id if prepared else None,
                            row["start_seconds"],
                            row["end_seconds"],
                            timestamp,
                            timestamp,
                        ),
                    )
                    connection.execute(
                        "UPDATE monitoring_windows SET state='submitted',prepared_input_id=?,job_id=? WHERE id=?",
                        (prepared.id if prepared else None, job_id, row["id"]),
                    )
                    connection.commit()
        except (ValueError, OSError, av.error.FFmpegError) as exc:
            with self.repository._connect() as connection:
                connection.execute(
                    "UPDATE monitoring_windows SET state='failed',reason=? WHERE id=? AND state='preparing'",
                    (str(exc), row["id"]),
                )
        return True
