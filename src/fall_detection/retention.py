"""Conservative pruning of unreferenced managed media and of ended monitoring sessions."""

import json
import shutil
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from fall_detection.models import PreparedInput
from fall_detection.storage import storage_lock

_MEDIA_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}


type Signature = tuple[tuple[str, int, int, int, int], ...]


@dataclass(frozen=True)
class PruneCandidate:
    """One old, unreferenced resource selected under the maintenance lock."""

    kind: Literal["prepared", "media", "live"]
    path: Path
    reason: str
    signature: Signature
    video_ids: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()

    @property
    def size_bytes(self) -> int:
        """Bytes of the regular files this candidate would remove."""
        files = self.signature if self.kind == "media" else self.signature[1:]
        return sum(entry[3] for entry in files)

    @property
    def file_count(self) -> int:
        """Number of regular files this candidate would remove."""
        return len(self.signature) if self.kind == "media" else len(self.signature) - 1


@dataclass(frozen=True)
class PruneReport:
    """Eligibility and apply outcome for one maintenance invocation."""

    candidates: tuple[PruneCandidate, ...]
    removed: tuple[Path, ...]
    skipped: tuple[Path, ...]


def _signature(path: Path, root: Path, *, directory: bool) -> Signature | None:
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            return None
        entries = [path, *sorted(path.iterdir())] if directory else [path]
        signature = []
        for entry in entries:
            metadata = entry.lstat()
            if entry.is_symlink() or not entry.resolve().is_relative_to(root):
                return None
            expected = stat.S_ISDIR if entry == path and directory else stat.S_ISREG
            if not expected(metadata.st_mode):
                return None
            signature.append(
                (
                    entry.name,
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                )
            )
        return tuple(signature)
    except (OSError, RuntimeError):
        return None


def _old_enough(signature: Signature, cutoff_ns: int) -> bool:
    return all(entry[4] < cutoff_ns for entry in signature)


def _created_before(value: str, cutoff: datetime) -> bool:
    try:
        created = datetime.fromisoformat(value)
        return created.tzinfo is not None and created.astimezone(UTC) < cutoff
    except ValueError:
        return False


def _ended_sessions(
    connection: sqlite3.Connection, session_cutoff: datetime | None
) -> dict[str, dict]:
    """Stopped sessions that ended before the cutoff and whose windows need no input.

    A session qualifies only when it is ``stopped`` (running and paused sessions
    never do), its last lifecycle change is older than the cutoff, no window is
    ``pending`` or ``preparing``, and no job of its windows is queued or running.
    Monitoring jobs cannot be retried, so a finished window never needs its input
    again; a restart schedules a new generation from the source or the live edge.
    """
    if session_cutoff is None:
        return {}
    rows = connection.execute(
        """SELECT * FROM monitoring_sessions s WHERE s.state='stopped'
        AND NOT EXISTS (SELECT 1 FROM monitoring_windows w WHERE w.session_id=s.id
            AND w.state IN ('pending','preparing'))
        AND NOT EXISTS (SELECT 1 FROM monitoring_windows w JOIN jobs j ON j.id=w.job_id
            WHERE w.session_id=s.id AND j.state IN ('queued','running'))"""
    ).fetchall()
    return {
        row["id"]: dict(row) for row in rows if _created_before(row["updated_at"], session_cutoff)
    }


def _live_candidates(
    root: Path,
    connection: sqlite3.Connection,
    ended: dict[str, dict],
    blocked_videos: set[str],
    cutoff_ns: int,
) -> list[PruneCandidate]:
    """Raw frame logs of live sources whose every session ended long enough ago."""
    live_root = root / "live"
    if not live_root.is_dir() or live_root.is_symlink():
        return []
    by_video: dict[str, list[str]] = {}
    for session_id, row in ended.items():
        if row.get("source_kind") == "live":
            by_video.setdefault(row["video_id"], []).append(session_id)
    candidates: list[PruneCandidate] = []
    for video_id, session_ids in sorted(by_video.items()):
        video = connection.execute(
            "SELECT source, storage_key FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        if (
            video_id in blocked_videos
            or video is None
            or video["source"] != "live"
            or video["storage_key"] != f"live/{video_id}"
        ):
            continue
        path = live_root / video_id
        if path.parent != live_root or not path.is_dir():
            continue
        signature = _signature(path, root, directory=True)
        if signature is None or not _old_enough(signature, cutoff_ns):
            continue
        candidates.append(
            PruneCandidate(
                "live",
                path,
                "raw live frames of an ended monitoring session",
                signature,
                (video_id,),
                tuple(sorted(session_ids)),
            )
        )
    return candidates


def _collect_candidates(
    root: Path,
    connection: sqlite3.Connection,
    cutoff: datetime,
    session_cutoff: datetime | None = None,
) -> tuple[PruneCandidate, ...]:
    cutoff_ns = int(cutoff.timestamp() * 1_000_000_000)
    jobs = connection.execute("SELECT video_id, prepared_input_id FROM jobs").fetchall()
    protected_videos = {row["video_id"] for row in jobs}
    protected_bundles = {row["prepared_input_id"] for row in jobs if row["prepared_input_id"]}
    # Bundles referenced only by windows (or their jobs) of ended sessions.
    session_bundles: dict[str, set[str]] = {}
    ended: dict[str, dict] = {}
    blocked_live_videos: set[str] = set()
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='monitoring_sessions'"
    ).fetchone():
        ended = _ended_sessions(connection, session_cutoff)
        sessions = connection.execute("SELECT id, video_id FROM monitoring_sessions").fetchall()
        protected_videos.update(row["video_id"] for row in sessions)
        blocked_live_videos = {row["video_id"] for row in sessions if row["id"] not in ended}
        references = connection.execute(
            """SELECT j.video_id, j.prepared_input_id, w.session_id FROM jobs j
            LEFT JOIN monitoring_windows w ON w.job_id = j.id
            UNION ALL SELECT s.video_id, w.prepared_input_id, w.session_id
            FROM monitoring_windows w JOIN monitoring_sessions s ON s.id = w.session_id"""
        ).fetchall()
        protected_bundles = set()
        for row in references:
            owner = row["session_id"] if row["session_id"] in ended else None
            if owner is None:
                blocked_live_videos.add(row["video_id"])
            if not row["prepared_input_id"]:
                continue
            if owner is None:
                protected_bundles.add(row["prepared_input_id"])
            else:
                session_bundles.setdefault(row["prepared_input_id"], set()).add(owner)
        for bundle_id in protected_bundles:
            session_bundles.pop(bundle_id, None)
    videos_by_key: dict[str, list[sqlite3.Row]] = {}
    for row in connection.execute("SELECT id, source, storage_key, created_at FROM videos"):
        if row["storage_key"]:
            videos_by_key.setdefault(row["storage_key"], []).append(row)

    candidates: list[PruneCandidate] = []
    protected_by_bundle: set[str] = set()
    prepared_root = root / "prepared"
    unknown_bundle_reference = prepared_root.is_symlink()
    if prepared_root.is_dir() and not prepared_root.is_symlink():
        for bundle in sorted(prepared_root.iterdir()):
            if len(bundle.name) != 64 or any(
                char not in "0123456789abcdef" for char in bundle.name
            ):
                unknown_bundle_reference = True
                continue
            signature = _signature(bundle, root, directory=True)
            if signature is None:
                unknown_bundle_reference = True
                continue
            manifest = bundle / "manifest.json"
            if not manifest.is_file() or manifest.stat().st_size > 2 * 1024 * 1024:
                unknown_bundle_reference = True
                continue
            raw = ""
            try:
                raw = manifest.read_text(encoding="utf-8")
                prepared = PreparedInput.model_validate_json(raw)
            except (OSError, ValueError):
                try:
                    video_id = json.loads(raw).get("video_id")
                    if isinstance(video_id, str):
                        protected_by_bundle.add(video_id)
                    else:
                        unknown_bundle_reference = True
                except (ValueError, AttributeError):
                    unknown_bundle_reference = True
                continue
            if prepared.id != bundle.name:
                protected_by_bundle.add(prepared.video_id)
                continue
            if prepared.id in protected_bundles or not _old_enough(signature, cutoff_ns):
                protected_by_bundle.add(prepared.video_id)
                continue
            if prepared.id in session_bundles:
                # Only the bundle goes; its source stays protected by the session record.
                protected_by_bundle.add(prepared.video_id)
                candidates.append(
                    PruneCandidate(
                        "prepared",
                        bundle,
                        "prepared input of an ended monitoring session",
                        signature,
                        (prepared.video_id,),
                        tuple(sorted(session_bundles[prepared.id])),
                    )
                )
                continue
            candidates.append(
                PruneCandidate(
                    "prepared",
                    bundle,
                    "old prepared bundle without a retained job",
                    signature,
                    (prepared.video_id,),
                )
            )

    media_root = root / "media"
    if not unknown_bundle_reference and media_root.is_dir() and not media_root.is_symlink():
        for media in sorted(media_root.iterdir()):
            if media.suffix.lower() not in _MEDIA_SUFFIXES:
                continue
            try:
                identity = UUID(media.stem)
                if identity.version != 4 or str(identity) != media.stem:
                    continue
            except ValueError:
                continue
            signature = _signature(media, root, directory=False)
            if signature is None or not _old_enough(signature, cutoff_ns):
                continue
            key = media.relative_to(root).as_posix()
            records = videos_by_key.get(key, [])
            if any(
                row["source"] != "upload"
                or row["id"] in protected_videos
                or row["id"] in protected_by_bundle
                or not _created_before(row["created_at"], cutoff)
                for row in records
            ):
                continue
            candidates.append(
                PruneCandidate(
                    "media",
                    media,
                    "old uploaded media without a retained job" if records else "old orphan upload",
                    signature,
                    tuple(row["id"] for row in records),
                )
            )
    candidates.extend(_live_candidates(root, connection, ended, blocked_live_videos, cutoff_ns))
    return tuple(candidates)


def prune_media(
    data_dir: Path,
    database_path: Path,
    *,
    min_age: timedelta = timedelta(days=7),
    session_age: timedelta | None = timedelta(days=30),
    apply: bool = False,
    now: datetime | None = None,
) -> PruneReport:
    """List eligible resources, or remove them with an explicit apply flag.

    Besides old unreferenced uploads and bundles, this covers prepared inputs and
    raw live frames of monitoring sessions stopped more than ``session_age`` ago
    (``None`` keeps all session data). Every removed file must also be older than
    ``min_age``. Prepared input IDs, hashes and ``live_frames`` rows stay in the
    database, so provenance survives and exports report pruned inputs as
    ``unavailable``.
    """
    if min_age <= timedelta(0):
        raise ValueError("minimum age must be positive")
    if session_age is not None and session_age <= timedelta(0):
        raise ValueError("session age must be positive")
    if data_dir.is_symlink():
        raise ValueError("managed data directory cannot be a symlink")
    if not data_dir.exists():
        return PruneReport((), (), ())
    root = data_dir.resolve(strict=True)
    if not database_path.is_file():
        raise FileNotFoundError(f"repository database is missing: {database_path}")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("current time must include a timezone")
    cutoff = current.astimezone(UTC) - min_age
    session_cutoff = None if session_age is None else current.astimezone(UTC) - session_age
    database_target = database_path if apply else f"{database_path.resolve().as_uri()}?mode=ro"
    with (
        storage_lock(root, exclusive=True),
        closing(sqlite3.connect(database_target, timeout=10, uri=not apply)) as connection,
    ):
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        candidates = _collect_candidates(root, connection, cutoff, session_cutoff)
        if not apply:
            connection.rollback()
            return PruneReport(candidates, (), ())

        removed: list[Path] = []
        skipped: list[Path] = []
        blocked_videos: set[str] = set()
        for candidate in candidates:
            if candidate.kind == "media":
                continue
            if _signature(candidate.path, root, directory=True) != candidate.signature:
                skipped.append(candidate.path)
                blocked_videos.update(candidate.video_ids)
                continue
            shutil.rmtree(candidate.path)
            removed.append(candidate.path)

        removable_media: list[PruneCandidate] = []
        for candidate in candidates:
            if candidate.kind != "media":
                continue
            if (
                blocked_videos.intersection(candidate.video_ids)
                or _signature(candidate.path, root, directory=False) != candidate.signature
            ):
                skipped.append(candidate.path)
                continue
            for video_id in candidate.video_ids:
                connection.execute("DELETE FROM videos WHERE id = ?", (video_id,))
            removable_media.append(candidate)
        connection.commit()
        for candidate in removable_media:
            if _signature(candidate.path, root, directory=False) != candidate.signature:
                skipped.append(candidate.path)
                continue
            candidate.path.unlink()
            removed.append(candidate.path)
        return PruneReport(candidates, tuple(removed), tuple(skipped))
