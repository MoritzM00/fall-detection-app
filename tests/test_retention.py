"""Retention behavior uses disposable managed trees only."""

import json
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from fall_detection.repository import Repository
from fall_detection.retention import prune_media
from fall_detection.storage_lock import storage_lock

NOW = datetime(2026, 9, 25, tzinfo=UTC)
OLD = NOW - timedelta(days=10)


def setup_storage(tmp_path: Path) -> tuple[Path, Repository]:
    root = tmp_path / "data"
    root.mkdir()
    repository = Repository(root / "app.sqlite3")
    repository.initialize()
    return root, repository


def upload(root: Path, repository: Repository, *, old: bool = True):
    path = root / "media" / f"{uuid4()}.mp4"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"disposable media")
    video = repository.create_uploaded_video(path.name, path.relative_to(root).as_posix())
    if old:
        with sqlite3.connect(root / "app.sqlite3") as db:
            db.execute("UPDATE videos SET created_at = ? WHERE id = ?", (OLD.isoformat(), video.id))
        set_age(path)
    return video, path


def set_age(path: Path) -> None:
    when = OLD.timestamp()
    os.utime(path, (when, when))


def bundle(root: Path, video_id: str, *, old: bool = True) -> Path:
    target = root / "prepared" / ("a" * 64)
    target.mkdir(parents=True)
    manifest = {
        "id": target.name,
        "video_id": video_id,
        "source_sha256": "b" * 64,
        "start_seconds": 0,
        "end_seconds": 1,
        "frame_count": 2,
        "fps": 1,
        "size": 224,
        "preprocessing_version": "test",
        "bundle_sha256": "c" * 64,
        "frames": [],
    }
    (target / "manifest.json").write_text(json.dumps(manifest))
    (target / "frames.npy").write_bytes(b"disposable frames")
    if old:
        for path in target.iterdir():
            set_age(path)
        set_age(target)
    return target


def test_dry_run_and_apply_remove_only_old_unreferenced_resources(tmp_path: Path) -> None:
    root, repository = setup_storage(tmp_path)
    video, media = upload(root, repository)
    prepared = bundle(root, video.id)
    unknown = root / "media" / "keep.txt"
    unknown.write_bytes(b"unmanaged")
    set_age(unknown)

    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    report = prune_media(root, root / "app.sqlite3", now=NOW)
    assert {candidate.path for candidate in report.candidates} == {media, prepared}
    assert not report.removed
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == before
    assert repository.get_video(video.id) is not None

    applied = prune_media(root, root / "app.sqlite3", now=NOW, apply=True)
    assert set(applied.removed) == {media, prepared}
    assert repository.get_video(video.id) is None
    assert unknown.exists()


@pytest.mark.parametrize(
    "state", ["queued", "running", "succeeded", "failed", "cancelled", "skipped"]
)
def test_all_retained_jobs_protect_media_and_prepared_bundle(tmp_path: Path, state: str) -> None:
    root, repository = setup_storage(tmp_path)
    video, media = upload(root, repository)
    prepared = bundle(root, video.id)
    job = repository.create_job(video.id, 0, 1, prepared_input_id=prepared.name)
    with sqlite3.connect(root / "app.sqlite3") as db:
        db.execute("UPDATE jobs SET state = ? WHERE id = ?", (state, job.id))
    report = prune_media(root, root / "app.sqlite3", now=NOW, apply=True)
    assert report.candidates == ()
    assert media.exists() and prepared.exists()


def test_fresh_bundle_protects_its_old_source_and_fresh_upload_stays(tmp_path: Path) -> None:
    root, repository = setup_storage(tmp_path)
    video, media = upload(root, repository)
    prepared = bundle(root, video.id, old=False)
    _, fresh_media = upload(root, repository, old=False)
    assert not prune_media(root, root / "app.sqlite3", now=NOW, apply=True).candidates
    assert media.exists() and fresh_media.exists() and prepared.exists()


def test_unknown_manifest_blocks_media_deletion(tmp_path: Path) -> None:
    root, repository = setup_storage(tmp_path)
    _, media = upload(root, repository)
    prepared = root / "prepared" / ("a" * 64)
    prepared.mkdir(parents=True)
    (prepared / "manifest.json").write_text("not json")
    set_age(prepared / "manifest.json")
    set_age(prepared)
    assert not prune_media(root, root / "app.sqlite3", now=NOW, apply=True).candidates
    assert media.exists()


def test_shared_upload_remains_when_another_video_has_a_job(tmp_path: Path) -> None:
    root, repository = setup_storage(tmp_path)
    video, media = upload(root, repository)
    second = repository.create_uploaded_video("same.mp4", video.storage_key)
    repository.create_job(second.id, 0, 1)
    with sqlite3.connect(root / "app.sqlite3") as db:
        db.execute("UPDATE videos SET created_at = ? WHERE id = ?", (OLD.isoformat(), second.id))
    assert not prune_media(root, root / "app.sqlite3", now=NOW, apply=True).candidates
    assert media.exists() and repository.get_video(video.id) is not None


def test_only_recognized_orphan_uploads_are_pruned(tmp_path: Path) -> None:
    root, _ = setup_storage(tmp_path)
    media_root = root / "media"
    media_root.mkdir()
    orphan = media_root / f"{uuid4()}.mp4"
    orphan.write_bytes(b"orphan")
    set_age(orphan)
    unknown = media_root / "unmanaged.mp4"
    unknown.write_bytes(b"unknown")
    set_age(unknown)
    report = prune_media(root, root / "app.sqlite3", now=NOW, apply=True)
    assert report.removed == (orphan,)
    assert unknown.exists()


def test_symlinks_and_unmanaged_files_are_never_removed(tmp_path: Path) -> None:
    root, repository = setup_storage(tmp_path)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    media = root / "media"
    media.mkdir()
    link = media / f"{uuid4()}.mp4"
    link.symlink_to(outside)
    prepared = root / "prepared"
    prepared.mkdir()
    (prepared / ("a" * 64)).symlink_to(tmp_path, target_is_directory=True)
    assert not prune_media(root, root / "app.sqlite3", now=NOW, apply=True).candidates
    assert link.is_symlink() and outside.read_bytes() == b"outside"
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        prune_media(alias, root / "app.sqlite3", now=NOW)


def test_cleanup_waits_for_writer_and_rechecks_age(tmp_path: Path) -> None:
    root, repository = setup_storage(tmp_path)
    _, media = upload(root, repository)
    entered = threading.Event()
    release = threading.Event()
    result = []

    def writer() -> None:
        with storage_lock(root, exclusive=False):
            entered.set()
            assert release.wait(5)
            media.write_bytes(b"updated while writer owns the lock")

    def cleanup() -> None:
        result.append(prune_media(root, root / "app.sqlite3", now=NOW, apply=True))

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    assert entered.wait(5)
    cleanup_thread = threading.Thread(target=cleanup)
    cleanup_thread.start()
    time.sleep(0.05)
    assert cleanup_thread.is_alive()
    release.set()
    writer_thread.join(timeout=5)
    cleanup_thread.join(timeout=5)
    assert not cleanup_thread.is_alive()
    assert not result[0].candidates
    assert media.exists()
