import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from fall_detection.pipeline import PipelineResult
from fall_detection.repository import Repository


def test_job_lifecycle_is_persisted(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.sqlite3")
    repository.initialize()
    video = repository.create_sample_video()
    queued = repository.create_job(video.id, 0, 2)

    claimed = repository.claim_next_job()
    assert claimed is not None
    assert claimed.id == queued.id
    assert claimed.state == "running"
    assert claimed.attempt_count == 1
    assert repository.claim_next_job() is None

    repository.complete_job(
        claimed.id,
        PipelineResult(
            label="fall",
            raw_response="The best answer is: fall",
            sampled_timestamps=[0, 1, 2],
            request_duration_ms=12.5,
            total_duration_ms=14.0,
        ),
        backend_kind="mock",
        model="qwen3-vl-8b-instruct",
        fixture_version="test-v1",
    )

    completed = repository.get_job(claimed.id)
    assert completed is not None
    assert completed.state == "succeeded"
    assert completed.prediction is not None
    assert completed.prediction.label == "fall"
    assert completed.prediction.backend_kind == "mock"


def test_dataset_video_identity_is_stable(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.sqlite3")
    repository.initialize()

    first = repository.create_dataset_video("01.mp4", "omnifall/videos/demo/01.mp4")
    second = repository.create_dataset_video("01.mp4", "omnifall/videos/demo/01.mp4")

    assert first == second
    assert first.source == "dataset"
    assert first.storage_key == "omnifall/videos/demo/01.mp4"


def test_initialize_migrates_existing_video_source_constraint(tmp_path: Path) -> None:
    database_path = tmp_path / "app.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE videos (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('upload', 'synthetic')),
                storage_key TEXT,
                duration_seconds REAL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO videos VALUES ('old', 'old.mp4', 'upload', 'media/old.mp4', NULL, 'now')"
        )

    repository = Repository(database_path)
    repository.initialize()
    created = repository.create_dataset_video("01.mp4", "omnifall/videos/demo/01.mp4")

    assert created.source == "dataset"
    assert repository.get_video("old") is not None


@pytest.mark.parametrize("source", ["synthetic", "dataset"])
def test_concurrent_video_creation_returns_one_stable_asset(tmp_path: Path, source: str):
    repository = Repository(tmp_path / "app.sqlite3")
    repository.initialize()
    barrier = Barrier(8)

    def create(_):
        barrier.wait(timeout=10)
        if source == "synthetic":
            return repository.create_sample_video()
        return repository.create_dataset_video("01.mp4", "omnifall/videos/demo/01.mp4")

    with ThreadPoolExecutor(max_workers=8) as pool:
        videos = list(pool.map(create, range(8)))

    assert all(video == videos[0] for video in videos)
    assert repository.get_video(videos[0].id) == videos[0]
