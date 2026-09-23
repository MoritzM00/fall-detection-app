from dataclasses import replace
from pathlib import Path

import pytest

import apps.worker.main as worker
from fall_detection.config import Settings
from fall_detection.pipeline import PipelineResult
from fall_detection.repository import Repository


@pytest.fixture
def queued_job(tmp_path: Path):
    settings = replace(
        Settings.from_env(),
        data_dir=tmp_path,
        database_path=tmp_path / "app.sqlite3",
        backend_kind="mock",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    video = repository.create_sample_video()
    job = repository.create_job(video.id, 0, 2, model="queued-model")
    return settings, repository, job


def test_worker_uses_snapshotted_model(queued_job, monkeypatch):
    settings, repository, job = queued_job
    models = []

    def run(**kwargs):
        models.append(kwargs["model"])
        return PipelineResult("fall", "The best answer is: fall", [0, 2], 1, 2)

    monkeypatch.setattr(worker, "run_pipeline", run)
    worker.process_next_job(settings, repository)
    completed = repository.get_job(job.id)
    assert completed.state == "succeeded"
    assert models == ["queued-model"]
    assert completed.prediction.model == "queued-model"


def test_worker_records_media_read_failure(queued_job, monkeypatch):
    settings, repository, job = queued_job

    def unreadable(*args):
        raise PermissionError("media access denied")

    monkeypatch.setattr(worker, "to_video_data_url", unreadable)
    worker.process_next_job(settings, repository)
    failed = repository.get_job(job.id)
    assert failed.state == "failed"
    assert failed.prediction is None
    assert "media access denied" in failed.error


def test_worker_rejects_synthetic_video_for_real_inference(queued_job, monkeypatch):
    settings, repository, job = queued_job

    def unexpected_request(**kwargs):
        raise AssertionError("Real inference must not receive unprocessed input")

    monkeypatch.setattr(worker, "run_pipeline", unexpected_request)
    worker.process_next_job(replace(settings, backend_kind="vllm"), repository)
    failed = repository.get_job(job.id)
    assert failed.state == "failed"
    assert failed.prediction is None
    assert "Synthetic sample" in failed.error
