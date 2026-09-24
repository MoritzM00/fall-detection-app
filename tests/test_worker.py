from dataclasses import replace
from pathlib import Path

import pytest

import apps.worker.main as worker
from fall_detection.config import Settings
from fall_detection.inference import InferenceResponse
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
    assert "Queued backend mock" in failed.error


def test_saved_synthetic_sampling_and_generation_drive_request(tmp_path: Path, monkeypatch):
    settings = replace(
        Settings.from_env(), data_dir=tmp_path, database_path=tmp_path / "app.sqlite3"
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    video = repository.create_sample_video()
    job = repository.create_job(
        video.id,
        0,
        1,
        model="saved-model",
        preprocessing={"frames": 6, "fps": 5, "resize": 224, "crop": "center"},
        generation={"temperature": 0.4, "max_tokens": 19},
    )
    sent = {}

    def complete(_client, payload):
        sent.update(payload)
        return InferenceResponse("The best answer is: fall", "completion")

    monkeypatch.setattr(worker.InferenceClient, "complete", complete)
    assert worker.process_next_job(replace(settings, inference_model="changed-default"), repository)
    result = repository.get_job(job.id)
    assert result is not None and result.prediction is not None
    assert result.prediction.sampled_timestamps == [0, 0.2, 0.4, 0.6, 0.8, 1]
    assert result.prediction.model == "saved-model"
    assert sent["model"] == "saved-model"
    assert sent["temperature"] == 0.4
    assert sent["max_tokens"] == 19
