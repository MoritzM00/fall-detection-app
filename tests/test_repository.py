from pathlib import Path

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
