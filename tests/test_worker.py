import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import httpx
import pytest

import apps.worker.main as worker
from fall_detection.config import Settings
from fall_detection.prediction import InferenceResponse, PipelineResult
from fall_detection.repository import Repository


@pytest.fixture(autouse=True)
def mock_discovery(monkeypatch):
    from fall_detection.prediction import InferenceClient

    monkeypatch.setattr(InferenceClient, "discover_mock_identity", lambda self: "sample-v1")


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


def test_worker_fails_job_when_endpoint_redirects_off_origin(queued_job, route_inference):
    settings, repository, job = queued_job
    settings = replace(
        settings,
        inference_base_url="https://gpu.test/v1",
        inference_proxy_key="dummy-key",
        inference_proxy_secret="dummy-secret",
    )
    foreign = []

    def handler(request):
        if request.url.host != "gpu.test":
            foreign.append(request)
            return httpx.Response(
                200, json={"id": "x", "choices": [{"message": {"content": "fall"}}]}
            )
        return httpx.Response(
            307, headers={"Location": "http://elsewhere.test/v1/chat/completions"}
        )

    route_inference(handler)
    worker.process_next_job(settings, repository)
    failed = repository.get_job(job.id)
    assert failed.state == "failed"
    assert failed.prediction is None
    assert "Refusing redirect to http://elsewhere.test" in failed.error
    assert "dummy" not in failed.error
    assert foreign == []


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


def _succeed(**kwargs):
    return PipelineResult("fall", "The best answer is: fall", [0, 2], 1, 2)


@pytest.mark.parametrize("failing_step", ["claim", "prepare"])
def test_transient_database_error_does_not_end_worker_loop(queued_job, monkeypatch, failing_step):
    settings, repository, job = queued_job
    monkeypatch.setattr(worker, "run_pipeline", _succeed)
    failures = iter([sqlite3.OperationalError("database is locked")])

    def fail_once(original):
        def wrapped(*args, **kwargs):
            error = next(failures, None)
            if error is not None:
                raise error
            return original(*args, **kwargs)

        return wrapped

    if failing_step == "claim":
        monkeypatch.setattr(repository, "claim_next_job", fail_once(repository.claim_next_job))
    else:
        monkeypatch.setattr(
            worker.Monitoring, "prepare_next", fail_once(worker.Monitoring.prepare_next)
        )
    slept: list[float] = []

    def should_stop() -> bool:
        return repository.get_job(job.id).state == "succeeded" or len(slept) > 10

    worker.run_loop(settings, repository, should_stop, sleep=slept.append)
    assert repository.get_job(job.id).state == "succeeded"
    assert slept == [worker.IDLE_SECONDS]


def _run_loop_with(settings, repository, monkeypatch, outcomes, stop_after_sleeping=None):
    """Run the loop against scripted iteration outcomes and return the wait after each."""
    waits: list[float] = []
    remaining = list(outcomes)

    def process(*args):
        waits.append(0.0)
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def sleep(seconds: float) -> None:
        waits[-1] += seconds

    def should_stop() -> bool:
        if stop_after_sleeping is not None:
            return sum(waits) >= stop_after_sleeping
        return not remaining

    monkeypatch.setattr(worker, "process_next_job", process)
    worker.run_loop(settings, repository, should_stop, sleep=sleep)
    return waits


def test_worker_loop_backs_off_exponentially_and_resets(queued_job, monkeypatch):
    settings, repository, _job = queued_job
    outcomes = [KeyError("a"), TypeError("b"), RuntimeError("c"), False, KeyError("d"), False]
    waits = _run_loop_with(settings, repository, monkeypatch, outcomes)
    assert waits == pytest.approx([0.4, 0.8, 1.6, 0.4, 0.4, 0.4])


def test_worker_loop_backoff_is_capped_and_stops_promptly(queued_job, monkeypatch):
    settings, repository, _job = queued_job
    outcomes = [sqlite3.OperationalError("database is locked")] * 20
    waits = _run_loop_with(settings, repository, monkeypatch, outcomes, stop_after_sleeping=12)
    assert max(waits) == pytest.approx(worker.MAX_BACKOFF_SECONDS)
    # The stop request lands mid-backoff and ends the wait within one idle slice.
    assert 12 <= sum(waits) < 12 + worker.IDLE_SECONDS
    assert len(waits) == 6


def test_unexpected_job_error_fails_job_with_generic_message(queued_job, monkeypatch):
    settings, repository, job = queued_job

    def explode(**kwargs):
        raise KeyError("internal secret detail")

    monkeypatch.setattr(worker, "run_pipeline", explode)
    assert worker.process_next_job(settings, repository)
    failed = repository.get_job(job.id)
    assert failed.state == "failed"
    assert failed.prediction is None
    assert failed.error == worker.UNEXPECTED_FAILURE


def test_locked_database_after_inference_keeps_the_result(queued_job, monkeypatch):
    settings, repository, job = queued_job
    monkeypatch.setattr(worker, "run_pipeline", _succeed)
    failures = iter([sqlite3.OperationalError("database is locked")] * 2)
    complete = repository.complete_job

    def locked_twice(*args, **kwargs):
        error = next(failures, None)
        if error is not None:
            raise error
        return complete(*args, **kwargs)

    monkeypatch.setattr(repository, "complete_job", locked_twice)
    slept: list[float] = []
    monkeypatch.setattr(worker.time, "sleep", slept.append)
    assert worker.process_next_job(settings, repository)
    completed = repository.get_job(job.id)
    assert completed.state == "succeeded"
    assert completed.prediction is not None and completed.prediction.label == "fall"
    assert slept == pytest.approx([0.4, 0.8])


def test_saving_gives_up_after_the_retry_window(monkeypatch):
    monkeypatch.setattr(worker, "SAVE_RETRY_SECONDS", 2.0)
    slept: list[float] = []

    def locked() -> bool:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        worker.save_result(locked, sleep=slept.append)
    assert slept == pytest.approx([0.4, 0.8, 1.6])


class _TickingStop(Event):
    """Heartbeat stop event that returns immediately and advances a fake clock per tick."""

    def __init__(self, clock: dict[str, float], ticks: list[float]) -> None:
        super().__init__()
        self.clock = clock
        self.ticks = iter(ticks)

    def wait(self, timeout: float | None = None) -> bool:
        tick = next(self.ticks, None)
        if tick is None:
            return True
        self.clock["now"] = tick
        return False


def test_failed_heartbeat_followed_by_success_keeps_lease(tmp_path: Path, monkeypatch):
    start = datetime(2026, 10, 12, tzinfo=UTC)
    clock = {"now": 0.0}
    repository = Repository(
        tmp_path / "app.sqlite3", clock=lambda: start + timedelta(seconds=clock["now"])
    )
    repository.initialize()
    video = repository.create_sample_video()
    job = repository.create_job(video.id, 0, 2, model="queued-model")
    claimed = repository.claim_next_job()
    assert claimed is not None and claimed.claim_token is not None
    renew = repository.renew_claim
    failures = iter([sqlite3.OperationalError("database is locked")])

    def flaky_renew(job_id: str, claim_token: str) -> bool:
        error = next(failures, None)
        if error is not None:
            raise error
        return renew(job_id, claim_token)

    monkeypatch.setattr(repository, "renew_claim", flaky_renew)
    stop = _TickingStop(clock, [20, 40])
    worker.keep_claim_alive(repository, job.id, claimed.claim_token, stop)
    # The lease from t=0 ends at t=90; only the retried renewal at t=40 keeps it past t=120.
    clock["now"] = 120
    assert repository.complete_job(
        job.id,
        _succeed(),
        backend_kind="mock",
        model="queued-model",
        fixture_version="sample-v1",
        claim_token=claimed.claim_token,
    )


def test_heartbeat_stops_when_claim_is_lost(queued_job):
    _settings, repository, job = queued_job
    assert repository.claim_next_job() is not None
    stop = _TickingStop({}, [1, 2, 3])
    worker.keep_claim_alive(repository, job.id, "not-the-owner", stop)
    assert next(stop.ticks, None) == 2


def test_worker_keeps_the_saved_message_order(queued_job, monkeypatch):
    settings, repository, job = queued_job
    orders = []

    def run(**kwargs):
        orders.append(kwargs["video_first"])
        return PipelineResult("fall", "The best answer is: fall", [0, 2], 1, 2)

    monkeypatch.setattr(worker, "run_pipeline", run)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE configurations SET video_first=0 WHERE id=?", (job.configuration_id,)
        )
    assert worker.process_next_job(settings, repository)
    assert orders == [False]


def test_worker_sends_an_additional_model_to_its_own_endpoint(queued_job, monkeypatch):
    from fall_detection.config import InferenceEndpoint
    from fall_detection.prediction import InferenceClient

    settings, repository, job = queued_job
    urls = []

    def complete(client, _payload):
        urls.append(client._base_url)
        return InferenceResponse("The best answer is: fall", "completion")

    monkeypatch.setattr(InferenceClient, "complete", complete)
    routed = replace(
        settings,
        additional_endpoints=(InferenceEndpoint("queued-model", "http://127.0.0.1:9/v1"),),
    )
    assert worker.process_next_job(routed, repository)
    assert repository.get_job(job.id).state == "succeeded"
    assert urls == ["http://127.0.0.1:9/v1"]
