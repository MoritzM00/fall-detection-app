import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event

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
    assert claimed.claim_token is not None
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
        claim_token=claimed.claim_token,
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


def test_expired_claim_is_visible_and_stale_owner_cannot_write(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def clock() -> datetime:
        return now

    repository = Repository(tmp_path / "app.sqlite3", clock=clock)
    repository.initialize()
    video = repository.create_sample_video()
    queued = repository.create_job(video.id, 0, 2)
    first = repository.claim_next_job()
    assert first is not None and first.claim_token is not None
    assert repository.renew_claim(first.id, first.claim_token)
    now += timedelta(seconds=91)
    assert not repository.renew_claim(first.id, first.claim_token)
    interrupted = repository.get_job(first.id)
    assert interrupted is not None and interrupted.state == "failed"
    assert "Retry" in (interrupted.error or "")
    retried = repository.retry_job(queued.id)
    assert retried.configuration_id == queued.configuration_id
    assert retried.prepared_input_id == queued.prepared_input_id
    with pytest.raises(ValueError, match="Only a failed job"):
        repository.retry_job(queued.id)
    second = repository.claim_next_job()
    assert second is not None and second.claim_token is not None
    assert second.claim_token != first.claim_token
    assert second.attempt_count == 2
    result = PipelineResult("fall", "fall", [0, 2], 1, 2)
    assert not repository.complete_job(
        first.id,
        result,
        backend_kind="mock",
        model="old",
        fixture_version=None,
        claim_token=first.claim_token,
    )
    assert not repository.fail_job(first.id, "stale", first.claim_token)
    assert not repository.renew_claim(first.id, first.claim_token)
    stale = repository.get_job(first.id)
    assert stale is not None and stale.prediction is None
    assert repository.complete_job(
        second.id,
        result,
        backend_kind="mock",
        model="new",
        fixture_version=None,
        claim_token=second.claim_token,
    )
    completed = repository.get_job(second.id)
    assert completed is not None and completed.prediction is not None
    assert completed.prediction.model == "new"


def test_recent_jobs_keep_active_runs_beyond_terminal_limit(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.sqlite3")
    repository.initialize()
    video = repository.create_sample_video()
    first = repository.create_job(video.id, 0, 2)
    second = repository.create_job(video.id, 0, 2)
    third = repository.create_job(video.id, 0, 2)
    running = repository.claim_next_job()
    assert running is not None and running.id == first.id
    # Two queued jobs remain reachable even when the terminal history cap is one.
    assert {job.id for job in repository.list_jobs(1)} == {first.id, second.id, third.id}
    with pytest.raises(ValueError, match="limit"):
        repository.list_jobs(101)


@pytest.mark.parametrize("startup", range(10))
def test_concurrent_initialization_preserves_legacy_running_job(tmp_path: Path, startup) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE videos (id TEXT PRIMARY KEY, filename TEXT NOT NULL,
              source TEXT NOT NULL CHECK (source IN ('upload', 'synthetic')),
              storage_key TEXT, duration_seconds REAL, created_at TEXT NOT NULL);
            CREATE TABLE configurations (id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
              model TEXT NOT NULL, prompt_preset TEXT NOT NULL, prompt_text TEXT NOT NULL,
              preprocessing_json TEXT NOT NULL, generation_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE jobs (id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id),
              configuration_id TEXT NOT NULL REFERENCES configurations(id), state TEXT NOT NULL,
              start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
              attempt_count INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL);
            CREATE TABLE predictions (id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
              label TEXT NOT NULL, raw_response TEXT NOT NULL, sampled_timestamps_json TEXT NOT NULL,
              backend_kind TEXT NOT NULL, model TEXT NOT NULL, fixture_version TEXT,
              request_duration_ms REAL NOT NULL, total_duration_ms REAL NOT NULL, completed_at TEXT NOT NULL);
            INSERT INTO videos VALUES ('v', 'old.mp4', 'upload', 'media/old.mp4', NULL, 'now');
            INSERT INTO configurations VALUES ('c', 1, 'old-model', 'baseline', 'old prompt',
              '{"frames":16,"resize":448,"crop":"center"}',
              '{"temperature":0,"max_tokens":32}', 'now');
            INSERT INTO jobs VALUES ('j', 'v', 'c', 'running', 0, 2, 1, NULL, 'now', 'now');
        """)
    barrier = Barrier(8)

    def initialize(_):
        barrier.wait(timeout=10)
        Repository(database).initialize()

    repository = Repository(database)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(initialize, range(8)))
    job = repository.get_job("j")
    assert job is not None and job.state == "failed"
    assert job.configuration is not None
    assert job.configuration.backend_kind == "unknown"
    assert job.configuration.model == "old-model"
    assert job.configuration.generation.max_tokens == 32
    assert repository.create_dataset_video("new.mp4", "omnifall/new.mp4").source == "dataset"


@pytest.mark.parametrize("startup", range(10))
def test_concurrent_first_start_creates_usable_wal_database(tmp_path: Path, startup) -> None:
    database = tmp_path / "new.sqlite3"
    barrier = Barrier(8)

    def initialize(_):
        barrier.wait(timeout=10)
        repository = Repository(database)
        repository.initialize()
        return repository.create_sample_video()

    with ThreadPoolExecutor(max_workers=8) as pool:
        videos = list(pool.map(initialize, range(8)))
    assert all(video == videos[0] for video in videos)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("operation", ["complete", "fail", "renew"])
def test_owner_transition_checks_clock_after_acquiring_write_lock(
    tmp_path: Path, operation: str
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def clock() -> datetime:
        return now

    database = tmp_path / "app.sqlite3"
    repository = Repository(database, clock=clock)
    repository.initialize()
    video = repository.create_sample_video()
    repository.create_job(video.id, 0, 2)
    claimed = repository.claim_next_job()
    assert claimed is not None and claimed.claim_token is not None
    claim_token = claimed.claim_token
    now += timedelta(seconds=89)
    started = Event()

    def try_transition() -> bool:
        started.set()
        if operation == "complete":
            return repository.complete_job(
                claimed.id,
                PipelineResult("fall", "fall", [0, 2], 1, 2),
                backend_kind="mock",
                model="m",
                fixture_version=None,
                claim_token=claim_token,
            )
        if operation == "fail":
            return repository.fail_job(claimed.id, "late", claim_token)
        return repository.renew_claim(claimed.id, claim_token)

    with sqlite3.connect(database, isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            attempt = pool.submit(try_transition)
            assert started.wait(2)
            now += timedelta(seconds=2)
            blocker.commit()
            assert not attempt.result(timeout=5)
    interrupted = repository.get_job(claimed.id)
    assert interrupted is not None and interrupted.state == "failed"
    assert interrupted.prediction is None


def test_claim_does_not_return_a_new_owners_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def clock() -> datetime:
        return now

    database = tmp_path / "app.sqlite3"
    first_repository = Repository(database, clock=clock)
    second_repository = Repository(database, clock=clock)
    first_repository.initialize()
    video = first_repository.create_sample_video()
    job = first_repository.create_job(video.id, 0, 2)
    entered_read = Event()
    resume_read = Event()
    original_get_job = first_repository.get_job

    def delayed_get_job(job_id: str):
        entered_read.set()
        assert resume_read.wait(2)
        return original_get_job(job_id)

    monkeypatch.setattr(first_repository, "get_job", delayed_get_job)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(first_repository.claim_next_job)
        assert entered_read.wait(2)
        now += timedelta(seconds=91)
        interrupted = second_repository.get_job(job.id)
        assert interrupted is not None and interrupted.state == "failed"
        second_repository.retry_job(job.id)
        second = second_repository.claim_next_job()
        assert second is not None and second.claim_token is not None
        resume_read.set()
        assert first.result(timeout=5) is None
