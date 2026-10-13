"""Proposed recorded replay behavior; independent of research inference policy."""

import json
import sqlite3
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from fall_detection.models import GenerationConfiguration, RunConfiguration, SamplingConfiguration
from fall_detection.prompts import default_preset, preset_id
from fall_detection.repository import Repository
from fall_detection.scheduling import LifecycleReason, ReplayScheduler


class SessionConfiguration(BaseModel):
    """Editable inference settings; replay policy is fixed at session creation."""

    video_id: str
    frame_count: int = Field(default=16, ge=2, le=32)
    fps: float = Field(default=7.5, gt=0, le=30, allow_inf_nan=False)
    size: int = Field(default=448, ge=224, le=672)
    model: str | None = Field(default=None, min_length=1)
    prompt_text: str | None = Field(default=None, min_length=1, max_length=16000)
    chain_of_thought: bool = False
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


LIVE_LENGTH_REACHED = "This live session has reached its maximum length; start a new session."


def live_edge(
    repository: Repository, connection: sqlite3.Connection, session: sqlite3.Row
) -> float:
    """Timeline position where a live session's next capture run would begin.

    Before any frame this is the session position. Afterwards it is the previous
    run's last frame plus the time elapsed on the server clock since that frame was
    received, and at least one capture interval, so pauses and stops stay real gaps
    and count against the session's maximum length.
    """
    last = connection.execute(
        """SELECT capture_seconds,received_at FROM live_frames
        WHERE video_id=? ORDER BY seq DESC LIMIT 1""",
        (session["video_id"],),
    ).fetchone()
    if last is None:
        return session["position"]
    elapsed = (repository.now() - datetime.fromisoformat(last["received_at"])).total_seconds()
    return last["capture_seconds"] + max(elapsed, 1 / session["capture_fps"])


def cancel_unprepared(
    connection: sqlite3.Connection,
    reason: LifecycleReason,
    timestamp: str,
    session_id: str | None = None,
) -> None:
    """Skip windows not yet prepared or claimed and cancel their queued jobs.

    The one owner of this fence: a lifecycle command passes its session, process
    recovery passes none and fences every session. Cancelled jobs keep the reason
    code as their error.
    """
    scope = (session_id, session_id)
    connection.execute(
        """UPDATE monitoring_windows SET state='skipped',reason=?
        WHERE (? IS NULL OR session_id=?) AND state IN ('pending','preparing')""",
        (reason, *scope),
    )
    connection.execute(
        """UPDATE monitoring_windows SET state='skipped',reason=? WHERE (? IS NULL OR session_id=?)
        AND job_id IN (SELECT id FROM jobs WHERE state='queued')""",
        (reason, *scope),
    )
    connection.execute(
        """UPDATE jobs SET state='cancelled',error=?,updated_at=? WHERE state='queued'
        AND id IN (SELECT job_id FROM monitoring_windows WHERE ? IS NULL OR session_id=?)""",
        (reason, timestamp, *scope),
    )


class Monitoring(ReplayScheduler):
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
        preset = default_preset(request.chain_of_thought)
        prompt = request.prompt_text or preset.prompt
        return RunConfiguration(
            model=model,
            backend_kind=serving_backend,
            fixture_version=fixture,
            prompt_preset=preset_id(prompt, request.chain_of_thought),
            prompt_text=prompt,
            chain_of_thought=request.chain_of_thought,
            preprocessing=SamplingConfiguration(
                frames=request.frame_count, fps=request.fps, resize=request.size
            ),
            generation=request.generation or preset.generation,
        )

    def create(
        self,
        request: SessionCreate,
        configuration: RunConfiguration,
        *,
        capture_fps: float | None = None,
    ) -> dict:
        """Create a paused session and its immutable initial configuration segment.

        A ``capture_fps`` makes it a live session: ``request.video_id`` names a new
        frame-log source, created in the same transaction so a failure leaves no
        orphan. Its bound is the maximum session length and its watermark follows
        ingested frames instead of playback.
        """
        session_id, segment = str(uuid4()), str(uuid4())
        timestamp = self.repository.timestamp()
        with self.repository.transaction() as connection:
            if capture_fps is not None:
                connection.execute(
                    """INSERT INTO videos
                    (id, filename, source, storage_key, duration_seconds, created_at)
                    VALUES (?, 'Live camera', 'live', ?, NULL, ?)""",
                    (request.video_id, f"live/{request.video_id}", timestamp),
                )
                video = None
            elif (
                connection.execute(
                    "SELECT id FROM videos WHERE id=?", (request.video_id,)
                ).fetchone()
                is None
            ):
                raise KeyError(request.video_id)
            else:
                video = self.repository.get_video(request.video_id)
                if video is not None and video.source == "live":
                    raise ValueError("Live sources require a live session")
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
            if capture_fps is not None:
                connection.execute(
                    """UPDATE monitoring_sessions SET source_kind='live',capture_fps=?,
                    duration_verified=1 WHERE id=?""",
                    (capture_fps, session_id),
                )
            if video is not None and video.source == "synthetic":
                connection.execute(
                    "UPDATE monitoring_sessions SET duration_verified=1 WHERE id=?", (session_id,)
                )
        return self.get(session_id)

    def get(self, session_id: str) -> dict:
        """Read lifecycle and the newest eligible result; preserve all history separately."""
        sessions = self._read_sessions("WHERE s.id=?", (session_id,))
        if not sessions:
            raise KeyError(session_id)
        return sessions[0]

    def recent(self, limit: int = 100) -> list[dict]:
        """Read the newest sessions like `get`, with one statement on one connection."""
        return self._read_sessions("ORDER BY s.created_at DESC LIMIT ?", (limit,))

    def _read_sessions(self, clause: str, parameters: tuple) -> list[dict]:
        with self.repository.transaction(immediate=False) as connection:
            rows = connection.execute(
                f"""SELECT s.*, seg.configuration_json AS _configuration_json,
                (SELECT w.job_id FROM monitoring_windows w JOIN jobs j ON j.id=w.job_id
                    WHERE w.session_id=s.id AND w.generation=s.generation
                    AND w.segment_id=s.segment_id AND j.state='succeeded'
                    ORDER BY w.sequence DESC LIMIT 1) AS _latest_job_id
                FROM monitoring_sessions s
                LEFT JOIN monitoring_segments seg ON seg.id=s.segment_id {clause}""",
                parameters,
            ).fetchall()
        sessions = []
        for row in rows:
            session = dict(row)
            session["configuration"] = json.loads(session.pop("_configuration_json"))
            session["latest_job_id"] = session.pop("_latest_job_id")
            sessions.append(session)
        return sessions

    def history(self, session_id: str, limit: int = 100, before: int | None = None) -> list[dict]:
        """Page immutable intervals, skipped coverage and input/job references."""
        self.get(session_id)
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self.repository.transaction(immediate=False) as connection:
            rows = connection.execute(
                """SELECT rowid AS cursor,* FROM monitoring_windows
                WHERE session_id=? AND (? IS NULL OR rowid < ?) ORDER BY rowid DESC LIMIT ?""",
                (session_id, before, before, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recover(self) -> None:
        """Pause replay on process startup; never silently resume a playback timeline."""
        timestamp = self.repository.timestamp()
        with self.repository.transaction() as connection:
            connection.execute(
                """UPDATE monitoring_sessions SET state='paused',
                recovery_reason=?,updated_at=? WHERE state='running'""",
                (LifecycleReason.PROCESS_RESTART, timestamp),
            )
            cancel_unprepared(connection, LifecycleReason.PROCESS_RESTART, timestamp)

    def command(
        self,
        session_id: str,
        command: SessionCommand,
        configuration: RunConfiguration | None = None,
    ) -> dict:
        """Apply timeline-preserving pause/resume or fence a changed playback generation."""
        with self.repository.transaction() as connection:
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
                    return self.get(session_id)
                connection.execute(
                    "INSERT INTO monitoring_commands VALUES (?,?,?)",
                    (session_id, command.command_id, command.model_dump_json()),
                )
            values = dict(row)
            action = command.action
            live = row["source_kind"] == "live"
            if live and (action in {"position", "seek"} or command.position_seconds is not None):
                raise ValueError("Live sessions follow ingested frames; position cannot be set")
            if (
                live
                and command.configuration is not None
                and command.configuration.fps > row["capture_fps"]
            ):
                raise ValueError("Sampling FPS cannot exceed the capture FPS")
            if (
                live
                and action in {"start", "resume", "restart"}
                and live_edge(self.repository, connection, row) >= row["duration"]
            ):
                # Every frame of a new run would exceed the cap; say so before capture starts.
                raise ValueError(LIVE_LENGTH_REACHED)
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
                if action == "pause" and row["state"] == "stopped":
                    raise ValueError("Stopped sessions require restart")
                values["state"] = "paused" if action == "pause" else "stopped"
            elif action == "restart":
                values["state"] = "running"
                # A live restart begins at the live edge; there is no earlier media to replay.
                values["position"] = row["position"] if live else command.position_seconds or 0
            elif action == "position" and command.position_seconds is None:
                raise ValueError("Position command requires an explicit position")
            if action in {"stop", "seek", "restart", "configure"}:
                cancel_unprepared(
                    connection, LifecycleReason(action), self.repository.timestamp(), session_id
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
                        self.repository.timestamp(),
                    ),
                )
                values["segment_id"] = segment
            # Pause drops only the not-yet-prepared candidate; resume uses the same cursor.
            if action == "pause":
                connection.execute(
                    "UPDATE monitoring_windows SET state='skipped',reason=? WHERE session_id=? AND state='pending'",
                    (LifecycleReason.PAUSE, session_id),
                )
            if live and values["state"] == "running" and row["state"] != "running":
                # Starting capture takes a moment; count the ingest timeout from here.
                values["last_frame_at"] = self.repository.timestamp()
            connection.execute(
                """UPDATE monitoring_sessions SET state=?,generation=?,segment_id=?,position=?,
                origin=?,next_sequence=?,updated_at=?,recovery_reason=NULL,last_frame_at=?
                WHERE id=?""",
                (
                    values["state"],
                    values["generation"],
                    values["segment_id"],
                    values["position"],
                    values["origin"],
                    values["next_sequence"],
                    self.repository.timestamp(),
                    values["last_frame_at"],
                    session_id,
                ),
            )
        return self.get(session_id)
