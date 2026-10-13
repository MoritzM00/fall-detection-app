"""Proposed replay scheduling: window admission, source checks, and preparation."""

import math
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from fall_detection.models import RunConfiguration
from fall_detection.repository import Repository
from fall_detection.storage import storage_lock

# Every value stored in monitoring_sessions.state, monitoring_sessions.source_kind and
# monitoring_windows.state. A window's job carries its own state (``models.JobState``).
type SessionState = Literal["running", "paused", "stopped"]
type SourceKind = Literal["recording", "live"]
type WindowState = Literal["pending", "preparing", "submitted", "skipped", "failed"]


class LifecycleReason(StrEnum):
    """Every fixed code written to window ``reason`` or session ``recovery_reason``.

    Writers bind these members instead of SQL literals, and exports keep exactly
    these codes; any other stored reason is a freeform diagnostic and is redacted.
    """

    PROCESS_RESTART = "process_restart"
    PREPARATION_INTERRUPTED = "preparation_interrupted"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    INCOMPLETE_TAIL = "incomplete_tail"
    LIFECYCLE_CHANGED = "lifecycle_changed"
    PAUSE = "pause"
    STOP = "stop"
    SEEK = "seek"
    RESTART = "restart"
    CONFIGURE = "configure"
    INGEST_GAP = "ingest_gap"
    CAPTURE_ENDED = "capture_ended"


class ReplayScheduler:
    """Admit completed playback intervals and prepare them as queued clip jobs."""

    repository: Repository

    def tick(self) -> None:
        """Admit only completed playback intervals, coalescing backlog into explicit coverage."""
        with self.repository.transaction() as connection:
            # Recovery must also sweep paused sessions before any later resume.
            abandoned = connection.execute(
                "SELECT id,session_id,created_at FROM monitoring_windows WHERE state='preparing'"
            ).fetchall()
            for candidate in abandoned:
                if (
                    self.repository.now() - datetime.fromisoformat(candidate["created_at"])
                ).total_seconds() > 90:
                    connection.execute(
                        "UPDATE monitoring_windows SET state='failed',reason=? WHERE id=?",
                        (LifecycleReason.PREPARATION_INTERRUPTED, candidate["id"]),
                    )
                    connection.execute(
                        "UPDATE monitoring_sessions SET state=CASE WHEN state='running' THEN 'paused' ELSE state END,recovery_reason=? WHERE id=?",
                        (LifecycleReason.PREPARATION_INTERRUPTED, candidate["session_id"]),
                    )
            queued = connection.execute("""SELECT w.*,s.position,s.expiration FROM monitoring_windows w
                JOIN monitoring_sessions s ON s.id=w.session_id JOIN jobs j ON j.id=w.job_id
                WHERE j.state='queued'""").fetchall()
            for candidate in queued:
                age = (
                    self.repository.now() - datetime.fromisoformat(candidate["created_at"])
                ).total_seconds()
                if (
                    age > candidate["expiration"]
                    or candidate["position"] - candidate["end_seconds"] > candidate["expiration"]
                ):
                    connection.execute(
                        "UPDATE jobs SET state='skipped',error='Monitoring window expired before claim',updated_at=? WHERE id=? AND state='queued'",
                        (self.repository.timestamp(), candidate["job_id"]),
                    )
                    connection.execute(
                        "UPDATE monitoring_windows SET state='skipped',reason=? WHERE id=?",
                        (LifecycleReason.EXPIRED, candidate["id"]),
                    )
            row = connection.execute(
                "SELECT * FROM monitoring_sessions WHERE state='running' AND duration_verified=1"
            ).fetchone()
            if row is None:
                return
            # Like playback end, a lost camera pauses only once admitted windows, whose
            # frames are already stored, have been prepared or have expired normally.
            if (
                row["source_kind"] == "live"
                and row["last_frame_at"] is not None
                and (
                    self.repository.now() - datetime.fromisoformat(row["last_frame_at"])
                ).total_seconds()
                > row["expiration"]
                and connection.execute(
                    "SELECT 1 FROM monitoring_windows WHERE session_id=? AND state IN ('pending','preparing')",
                    (row["id"],),
                ).fetchone()
                is None
            ):
                connection.execute(
                    """UPDATE monitoring_sessions SET state='paused',recovery_reason=?,
                    updated_at=? WHERE id=?""",
                    (LifecycleReason.CAPTURE_ENDED, self.repository.timestamp(), row["id"]),
                )
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
                return
            if pending is not None and (
                pending["sequence"] < last
                or row["position"] - pending["end_seconds"] > row["expiration"]
                or (
                    self.repository.now() - datetime.fromisoformat(pending["created_at"])
                ).total_seconds()
                > row["expiration"]
            ):
                connection.execute(
                    "UPDATE monitoring_windows SET state='skipped',reason=? WHERE id=?",
                    (LifecycleReason.EXPIRED, pending["id"]),
                )
                pending = None
            if seq <= last:
                if seq < last:
                    self._window(
                        connection, row, seq, last - 1, width, "skipped", LifecycleReason.SUPERSEDED
                    )
                end = row["origin"] + last * row["stride"] + width
                state: WindowState = (
                    "skipped" if row["position"] - end > row["expiration"] else "pending"
                )
                self._window(
                    connection,
                    row,
                    last,
                    last,
                    width,
                    state,
                    LifecycleReason.EXPIRED if state == "skipped" else None,
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
                    LifecycleReason.INCOMPLETE_TAIL,
                    end=row["duration"],
                )
                seq += 1
            connection.execute(
                "UPDATE monitoring_sessions SET next_sequence=? WHERE id=?", (seq, row["id"])
            )
            # Playback end pauses here, after admission, so the final window is not dropped.
            if (
                row["position"] >= row["duration"]
                and connection.execute(
                    "SELECT 1 FROM monitoring_windows WHERE session_id=? AND state IN ('pending','preparing')",
                    (row["id"],),
                ).fetchone()
                is None
            ):
                connection.execute(
                    "UPDATE monitoring_sessions SET state='paused',updated_at=? WHERE id=?",
                    (self.repository.timestamp(), row["id"]),
                )

    def _window(
        self,
        connection,
        row,
        seq,
        last,
        width,
        state: WindowState,
        reason: LifecycleReason | None,
        *,
        end=None,
    ):
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
                self.repository.timestamp(),
            ),
        )

    def verify_source(self, settings) -> None:
        """Read source playback bounds on the worker before any real-window admission."""
        import av

        with self.repository.transaction(immediate=False) as connection:
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
                    if stream is None:
                        raise ValueError("Replay requires a video stream")
                    duration = min(row["duration"], source_duration(container, stream))
                with self.repository.transaction() as connection:
                    current = connection.execute(
                        "SELECT origin,state FROM monitoring_sessions WHERE id=?", (row["id"],)
                    ).fetchone()
                    # Commands can move the timeline while source metadata is read.
                    if duration <= current["origin"]:
                        connection.execute(
                            "UPDATE monitoring_sessions SET state=CASE WHEN state='running' THEN 'paused' ELSE state END,recovery_reason='Replay starts beyond the source duration' WHERE id=?",
                            (row["id"],),
                        )
                    connection.execute(
                        "UPDATE monitoring_sessions SET duration=?,position=MIN(position,?),duration_verified=1 WHERE id=?",
                        (duration, duration, row["id"]),
                    )
        except (ValueError, OSError, av.error.FFmpegError) as exc:
            with self.repository.transaction(immediate=False) as connection:
                connection.execute(
                    "UPDATE monitoring_sessions SET state='paused',recovery_reason=? WHERE id=? AND state='running' AND generation=? AND segment_id=?",
                    (str(exc), row["id"], row["generation"], row["segment_id"]),
                )

    def prepare_next(self, settings) -> bool:
        """Prepare one candidate on the worker, yielding to all queued clip jobs."""
        import av

        from fall_detection.live import IngestGapError, prepare_frames
        from fall_detection.models import PreparationRequest
        from fall_detection.preparation import prepare_video

        self.verify_source(settings)
        self.tick()
        with self.repository.transaction() as connection:
            # Global monitoring work bound includes obsolete generations still running.
            if (
                connection.execute("""SELECT 1 FROM jobs WHERE state IN ('queued','running')
                AND id IN (SELECT job_id FROM monitoring_windows)""").fetchone()
                or connection.execute(
                    """SELECT 1 FROM jobs WHERE state='queued' AND id NOT IN
                (SELECT job_id FROM monitoring_windows WHERE job_id IS NOT NULL)"""
                ).fetchone()
            ):
                return False
            row = connection.execute("""SELECT w.*,s.video_id,s.capture_fps FROM monitoring_windows w
                JOIN monitoring_sessions s ON s.id=w.session_id WHERE w.state='pending'
                AND s.state='running' AND s.generation=w.generation AND s.segment_id=w.segment_id""").fetchone()
            if row is None:
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
        prepared = None
        try:
            video = self.repository.get_video(row["video_id"])
            if video is None:
                raise ValueError("Session source is missing")
            # Lock spans publication and durable reference, preventing prune races.
            with storage_lock(settings.data_dir, exclusive=False):
                if video.source == "live":
                    prepared = prepare_frames(
                        self.repository,
                        video,
                        PreparationRequest(
                            video_id=video.id,
                            start_seconds=row["start_seconds"],
                            frame_count=config.preprocessing.frames,
                            fps=config.preprocessing.fps,
                            size=config.preprocessing.resize,
                        ),
                        row["capture_fps"],
                        settings.data_dir,
                        settings.inspection_pngs,
                        rgb_array=settings.rgb_arrays,
                    )
                elif video.source != "synthetic":
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
                        rgb_array=settings.rgb_arrays,
                    )
                elif config.backend_kind != "mock":
                    raise ValueError("Synthetic replay requires mock serving")
                with self.repository.transaction() as connection:
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
                        self.repository.now() - datetime.fromisoformat(row["created_at"])
                    ).total_seconds() > current["expiration"] or current["position"] - row[
                        "end_seconds"
                    ] > current["expiration"]:
                        connection.execute(
                            "UPDATE monitoring_windows SET state='skipped',reason=? WHERE id=? AND state='preparing'",
                            (LifecycleReason.EXPIRED, row["id"]),
                        )
                        return True
                    if not eligible:
                        connection.execute(
                            "UPDATE monitoring_windows SET state='skipped',reason=COALESCE(reason,?) WHERE id=? AND state='preparing'",
                            (LifecycleReason.LIFECYCLE_CHANGED, row["id"]),
                        )
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
                    timestamp = self.repository.timestamp()
                    connection.execute(
                        """INSERT INTO configurations
                        (id,schema_version,model,prompt_preset,prompt_text,chain_of_thought,
                         preprocessing_json,generation_json,created_at,backend_kind,fixture_version)
                         VALUES (?,2,?,?,?,?,?,?,?,?,?)""",
                        (
                            configuration_id,
                            config.model,
                            config.prompt_preset,
                            config.prompt_text,
                            config.chain_of_thought,
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
        except IngestGapError:
            with self.repository.transaction(immediate=False) as connection:
                connection.execute(
                    "UPDATE monitoring_windows SET state='skipped',reason=? WHERE id=? AND state='preparing'",
                    (LifecycleReason.INGEST_GAP, row["id"]),
                )
        except (ValueError, OSError, av.error.FFmpegError) as exc:
            with self.repository.transaction(immediate=False) as connection:
                connection.execute(
                    "UPDATE monitoring_windows SET state='failed',reason=? WHERE id=? AND state='preparing'",
                    (str(exc), row["id"]),
                )
        return True


def source_duration(container, stream) -> float:
    """Return a replay source's playable length in seconds from its own metadata.

    MP4 and MOV store the length on the video stream. Matroska and WebM store it on
    the container instead. Streamed WebM, such as browser ``MediaRecorder`` output,
    may have neither, so the last video packet end is read once without decoding.
    """
    import av

    if stream.duration is not None and stream.time_base is not None:
        return float(stream.duration * stream.time_base)
    if container.duration is not None and container.duration > 0:
        return container.duration / av.time_base
    end = None
    if stream.time_base is not None:
        for packet in container.demux(stream):
            if packet.pts is not None:
                packet_end = packet.pts + max(packet.duration or 0, 0)
                end = packet_end if end is None else max(end, packet_end)
    start = stream.start_time or 0
    if end is None or end <= start:
        raise ValueError(
            "Replay source has no readable duration: neither its metadata nor its video "
            "packets give a length. Re-encode it as MP4 and upload it again."
        )
    return float((end - start) * stream.time_base)
