"""Local simulation contract coverage, not real vLLM compatibility evidence."""

import base64
import io
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Thread

import av
import numpy as np
import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image

import apps.api.main as api
import apps.mock_inference.main as mock
import apps.worker.main as worker
from apps.mock_inference.scenarios import (
    Manifest,
    Scenario,
    fingerprint,
    load_manifest,
    next_attempt,
)
from fall_detection.config import Settings
from fall_detection.inference import InferenceClient, InferenceServiceError
from fall_detection.models import PreparationRequest
from fall_detection.pipeline import build_inference_payload
from fall_detection.preparation import prepare_video
from fall_detection.repository import Repository
from fall_detection.taxonomy import ACTIVITY_LABELS


def payload(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, "JPEG")
    url = "data:video/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    return build_inference_payload(
        mock.MANIFEST.model,
        url,
        "prompt",
        {
            "fps": 5,
            "frames_indices": [0],
            "total_num_frames": 1,
            "duration": 1,
            "do_sample_frames": False,
        },
    )


@pytest.fixture
def set_manifest(monkeypatch):
    def configure(default=None, **kwargs):
        manifest = Manifest(default=default or Scenario(delay_ms=0), **kwargs)
        monkeypatch.setattr(mock, "MANIFEST", manifest)
        return manifest

    configure()
    return configure


@pytest.fixture
def http_service(set_manifest):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(mock.app, log_level="critical", lifespan="off"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.01)
            assert server.started
            yield f"http://127.0.0.1:{listener.getsockname()[1]}/v1"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.mark.parametrize("label", ACTIVITY_LABELS)
def test_all_labels_over_http(http_service, set_manifest, label):
    manifest = set_manifest(Scenario(label=label, delay_ms=0))
    client = InferenceClient(http_service, mock_fixture_identity=manifest.identity())
    assert client.discover_mock_identity() == manifest.identity()
    assert client.complete(payload()).content == f"The best answer is: {label}"


def test_effective_environment_identity(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {"default": {"delay_ms": 0}, "inputs": {"a" * 64: {"label": "standing", "delay_ms": 1}}}
        )
    )
    monkeypatch.setenv("MOCK_INFERENCE_MANIFEST", str(path))
    for key in ("MOCK_INFERENCE_LABEL", "MOCK_INFERENCE_DELAY_MS", "MOCK_INFERENCE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    baseline = load_manifest()
    for env, value in [
        ("MOCK_INFERENCE_LABEL", "walk"),
        ("MOCK_INFERENCE_DELAY_MS", "20"),
        ("MOCK_INFERENCE_MODEL", "different"),
    ]:
        monkeypatch.setenv(env, value)
        assert load_manifest().identity() != baseline.identity()
        monkeypatch.delenv(env)
    changed = baseline.model_copy(
        update={"inputs": {"a" * 64: Scenario(label="fallen", delay_ms=1)}}
    )
    assert changed.identity() != baseline.identity()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "count",
        "indices",
        "sample",
        "fps",
        "duration",
        "bytes",
        "png",
        "unsupported",
    ],
)
def test_prepared_validation(set_manifest, mutation):
    body = payload()
    metadata = body["media_io_kwargs"]["video"]
    if mutation == "missing":
        del body["media_io_kwargs"]
    elif mutation == "extra":
        metadata["unknown"] = True
    elif mutation == "count":
        metadata["total_num_frames"] = 2
    elif mutation == "indices":
        metadata["frames_indices"] = [False]
    elif mutation == "sample":
        metadata["do_sample_frames"] = True
    elif mutation in {"fps", "duration"}:
        metadata[mutation] = "invalid"
    else:
        video = body["messages"][0]["content"][1]["video_url"]
        if mutation == "bytes":
            video["url"] = "data:video/jpeg;base64,bW9jaw=="
        elif mutation == "png":
            buffer = io.BytesIO()
            Image.new("RGB", (16, 16)).save(buffer, "PNG")
            video["url"] = "data:video/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
        else:
            video["url"] = "https://example.com/video.mp4"
    assert TestClient(mock.app).post("/v1/chat/completions", json=body).status_code == 400


def test_concurrent_scripted_windows_are_input_driven(http_service, set_manifest):
    bodies = [payload(color) for color in ("red", "green", "blue")]
    mappings = {
        fingerprint(
            mock.validate_prepared_video(mock.ChatCompletionRequest.model_validate(body))
        ): Scenario(label=label, delay_ms=0)
        for body, label in zip(bodies, ("standing", "fall", "fallen"), strict=True)
    }
    manifest = set_manifest(inputs=mappings)
    client = InferenceClient(http_service, mock_fixture_identity=manifest.identity())
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(client.complete, bodies * 3))
    assert [r.content for r in responses] == [
        f"The best answer is: {label}" for label in ("standing", "fall", "fallen") * 3
    ]
    reversed_body = payload()
    reversed_body["messages"][0]["content"].reverse()
    assert client.complete(reversed_body).content == responses[0].content


def prepared_job(tmp_path, endpoint, manifest):
    settings = replace(
        Settings.from_env(),
        data_dir=tmp_path,
        database_path=tmp_path / "app.sqlite3",
        inference_base_url=endpoint,
        request_timeout_seconds=0.15,
        backend_kind="mock",
    )
    path = tmp_path / "media" / "generated.mp4"
    path.parent.mkdir()
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 32, 32, "yuv420p"
        for index in range(12):
            frame = av.VideoFrame.from_ndarray(
                np.full((32, 32, 3), index * 15, dtype=np.uint8), format="rgb24"
            )
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    repository = Repository(settings.database_path)
    repository.initialize()
    video = repository.create_uploaded_video("generated.mp4", "media/generated.mp4")
    prepared = prepare_video(
        video,
        PreparationRequest(video_id=video.id, start_seconds=0, frame_count=6, fps=5, size=224),
        tmp_path,
    )
    with TestClient(api.create_app(settings, repository)) as client:
        response = client.post(
            "/analysis-jobs",
            json={
                "video_id": video.id,
                "prepared_input_id": prepared.id,
                "start_seconds": 0,
                "end_seconds": 1,
                "frame_count": 6,
                "fps": 5,
                "size": 224,
            },
        )
        assert response.status_code == 202, response.text
        job = repository.get_job(response.json()["id"])
    assert job is not None and job.configuration is not None
    assert job.configuration.fixture_version == manifest.identity()
    return settings, repository, job, prepared


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "malformed",
        "invalid",
        "ambiguous",
        "truncated",
        "429",
        "500",
        "503",
        "interruption",
        "timeout",
        "stale",
    ],
)
def test_real_worker_parser_and_persistence(tmp_path, http_service, set_manifest, fault):
    scenario = Scenario(
        delay_ms=400 if fault == "timeout" else 0,
        fault=fault if fault not in {"timeout", "stale"} else "none",
    )
    manifest = set_manifest(scenario)
    settings, repository, job, prepared = prepared_job(tmp_path, http_service, manifest)
    if fault == "stale":
        set_manifest(Scenario(label="fallen", delay_ms=0))
    assert worker.process_next_job(settings, repository)
    completed = repository.get_job(job.id)
    if fault == "none":
        assert completed.state == "succeeded"
        assert completed.prediction.label == "fall"
        assert completed.prediction.backend_kind == "mock"
        assert completed.prediction.fixture_version == manifest.identity()
        assert completed.prediction.sampled_timestamps == [
            frame.actual_seconds for frame in prepared.frames
        ]
        assert completed.configuration.preprocessing.bundle_sha256 == prepared.bundle_sha256
    else:
        assert completed.state == "failed", completed
        assert completed.prediction is None
        assert completed.error
    assert not worker.process_next_job(settings, repository)  # No automatic retry.


def test_request_identity_fences_discovery_race(http_service, set_manifest):
    client = InferenceClient(http_service)
    saved = client.discover_mock_identity()
    set_manifest(Scenario(label="walk", delay_ms=0))
    with pytest.raises(InferenceServiceError, match="409"):
        InferenceClient(http_service, mock_fixture_identity=saved).complete(payload())


def test_durable_explicit_retry(tmp_path, http_service, set_manifest, monkeypatch):
    monkeypatch.setenv("MOCK_INFERENCE_ATTEMPT_DB", str(tmp_path / "attempts.sqlite3"))
    manifest = set_manifest(Scenario(delay_ms=0, fault="503", fail_first=1))
    settings, repository, job, _ = prepared_job(tmp_path, http_service, manifest)
    worker.process_next_job(settings, repository)
    assert repository.get_job(job.id).state == "failed"
    repository.retry_job(job.id)
    worker.process_next_job(settings, repository)
    assert repository.get_job(job.id).state == "succeeded"
    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = list(
            pool.map(
                lambda _: next_attempt(
                    tmp_path / "counts.sqlite3", manifest.identity(), "same-request"
                ),
                range(8),
            )
        )
    assert sorted(attempts) == list(range(1, 9))
    assert next_attempt(tmp_path / "counts.sqlite3", manifest.identity(), "same-request") == 9


def test_discovery_unavailable_rejects_submission(tmp_path, monkeypatch):
    settings = replace(
        Settings.from_env(), data_dir=tmp_path, database_path=tmp_path / "app.sqlite3"
    )
    repository = Repository(settings.database_path)

    def unavailable(self):
        raise InferenceServiceError("Fixture service unavailable")

    monkeypatch.setattr(InferenceClient, "discover_mock_identity", unavailable)
    with TestClient(api.create_app(settings, repository)) as client:
        video = client.post("/videos/sample").json()
        result = client.post("/analysis-jobs", json={"video_id": video["id"]})
        assert result.status_code == 503
        assert repository.claim_next_job() is None


def test_request_mapping_precedence_and_generation_identity(set_manifest):
    client = TestClient(mock.app)
    body = payload()
    discovery = client.post("/v1/chat/completions", json=body)
    input_key = discovery.headers["X-Mock-Input-Fingerprint"]
    request_key = discovery.headers["X-Mock-Request-Fingerprint"]
    set_manifest(
        inputs={input_key: Scenario(label="standing", delay_ms=0)},
        requests={request_key: Scenario(label="fallen", delay_ms=0)},
    )
    response = client.post("/v1/chat/completions", json=body)
    assert response.json()["choices"][0]["message"]["content"] == "The best answer is: fallen"
    body["temperature"] = 0.5
    response = client.post("/v1/chat/completions", json=body)
    assert response.headers["X-Mock-Input-Fingerprint"] == input_key
    assert response.headers["X-Mock-Request-Fingerprint"] != request_key
    assert response.json()["choices"][0]["message"]["content"] == "The best answer is: standing"


def test_service_outage_fails_queued_job(tmp_path, http_service, set_manifest):
    manifest = set_manifest()
    settings, repository, job, _ = prepared_job(tmp_path, http_service, manifest)
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{unavailable.getsockname()[1]}/v1"
        worker.process_next_job(replace(settings, inference_base_url=endpoint), repository)
    failed = repository.get_job(job.id)
    assert failed.state == "failed" and failed.prediction is None
    assert "discovery failed" in failed.error


@pytest.mark.parametrize(
    "body",
    [
        {"choices": ["bad"]},
        {
            "id": "x",
            "choices": [
                {"message": {"content": "The best answer is: fall"}, "finish_reason": "length"}
            ],
        },
    ],
)
def test_invalid_choice_and_truncated_valid_label_are_service_errors(monkeypatch, body):
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kwargs: httpx.Response(200, json=body, request=httpx.Request("POST", url)),
    )
    with pytest.raises(InferenceServiceError):
        InferenceClient("http://fixture/v1").complete(payload())


def test_missing_durable_retry_storage_is_explicit(set_manifest, monkeypatch):
    monkeypatch.delenv("MOCK_INFERENCE_ATTEMPT_DB", raising=False)
    set_manifest(Scenario(fault="503", fail_first=1, delay_ms=0))
    response = TestClient(mock.app).post("/v1/chat/completions", json=payload())
    assert response.status_code == 503
    assert "durable attempt database" in response.json()["detail"]


def test_discovery_rejects_manifest_identity_mismatch(monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: httpx.Response(
            200,
            json={
                "backend": "mock",
                "fixture_version": "mock-v1-sha256:unverified",
                "manifest": Manifest().model_dump(),
            },
            request=httpx.Request("GET", url),
        ),
    )
    with pytest.raises(InferenceServiceError, match="Invalid mock service identity"):
        InferenceClient("http://fixture/v1").discover_mock_identity()


def test_numeric_metadata_spelling_and_empty_output(set_manifest):
    client = TestClient(mock.app)
    body = payload()
    original = client.post("/v1/chat/completions", json=body)
    body["media_io_kwargs"]["video"]["fps"] = 5.0
    body["media_io_kwargs"]["video"]["duration"] = 1.0
    normalized = client.post("/v1/chat/completions", json=body)
    assert (
        normalized.headers["X-Mock-Input-Fingerprint"]
        == original.headers["X-Mock-Input-Fingerprint"]
    )
    assert (
        normalized.headers["X-Mock-Request-Fingerprint"]
        == original.headers["X-Mock-Request-Fingerprint"]
    )
    set_manifest(Scenario(output="", delay_ms=0))
    assert (
        client.post("/v1/chat/completions", json=body).json()["choices"][0]["message"]["content"]
        == ""
    )


@pytest.mark.parametrize(
    "manifest",
    [
        {"version": 2},
        {"inputs": {"invalid-fingerprint": {}}},
        {"default": {"label": "not-a-label"}},
        {"default": {"delay_ms": -1}},
        {"default": {"unsupported": True}},
    ],
)
def test_invalid_manifests_are_rejected(manifest):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Manifest.model_validate(manifest)
