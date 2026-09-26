import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import apps.api.main as api
from fall_detection.config import Settings
from fall_detection.inference import InferenceResponse
from fall_detection.prompts import PRESET_ID, THESIS_BASELINE_PROMPT
from fall_detection.repository import Repository
from fall_detection.upload_limit import MULTIPART_OVERHEAD_BYTES


@pytest.fixture
def make_api(tmp_path):
    def build(name="default", **overrides):
        data_dir = tmp_path / name
        settings = replace(
            Settings.from_env(),
            data_dir=data_dir,
            database_path=data_dir / "app.sqlite3",
            inference_model="custom-model",
        )
        settings = replace(settings, **overrides)
        repository = Repository(settings.database_path)
        return api.create_app(settings, repository), repository, data_dir

    return build


@pytest.fixture
def api_instance(make_api):
    return make_api()


@pytest.fixture
def client(api_instance):
    app, _, _ = api_instance
    with TestClient(app) as test_client:
        yield test_client


def test_api_snapshots_configured_model(client, api_instance):
    _, repository, _ = api_instance
    video = client.post("/videos/sample").json()
    response = client.post("/analysis-jobs", json={"video_id": video["id"]})
    assert response.status_code == 202
    model, _ = repository.get_inference_configuration(response.json()["configuration_id"])
    assert model == "custom-model"


@pytest.mark.parametrize("custom_response", ["The best answer is: walk", "walk"])
def test_api_experiments_preserve_old_runs_and_drive_worker_payload(
    client, api_instance, monkeypatch, custom_response
):
    from apps.worker.main import process_next_job
    from fall_detection.inference import InferenceClient

    _, repository, data_dir = api_instance
    video = client.post("/videos/sample").json()
    baseline = client.post("/analysis-jobs", json={"video_id": video["id"]}).json()
    prompt = (
        "Reply with only the activity label."
        if custom_response == "walk"
        else "  Classify the primary action. Respond only: The best answer is: <class_label>\n"
    )
    edited = client.post(
        "/analysis-jobs",
        json={
            "video_id": video["id"],
            "model": "custom-model",
            "prompt_text": prompt,
            "generation": {"temperature": 0.6, "max_tokens": 80},
        },
    )
    assert edited.status_code == 202
    edited = edited.json()
    assert edited["configuration_id"] != baseline["configuration_id"]
    assert edited["configuration"]["prompt_preset"] == "custom"
    assert edited["configuration"]["prompt_text"] == prompt
    original = client.get(f"/analysis-jobs/{baseline['id']}").json()
    assert original["configuration"]["prompt_text"] == THESIS_BASELINE_PROMPT
    assert original["configuration"]["prompt_preset"] == PRESET_ID
    assert original["configuration"]["generation"] == {"temperature": 0, "max_tokens": 32}

    sent = []

    def complete(_client, payload):
        sent.append(payload)
        return InferenceResponse(
            "The best answer is: walk" if len(sent) == 1 else custom_response, "completion"
        )

    monkeypatch.setattr(InferenceClient, "complete", complete)
    settings = replace(
        Settings.from_env(),
        data_dir=data_dir,
        database_path=data_dir / "app.sqlite3",
        inference_model="changed-runtime-model",
        backend_kind="mock",
    )
    assert process_next_job(settings, repository)
    assert process_next_job(settings, repository)
    assert sent[1]["model"] == "custom-model"
    assert sent[1]["messages"][0]["content"][0]["text"] == prompt
    assert sent[1]["temperature"] == 0.6 and sent[1]["max_tokens"] == 80
    completed = client.get(f"/analysis-jobs/{edited['id']}").json()
    assert completed["prediction"]["label"] == "walk"
    baseline_result = client.get(f"/analysis-jobs/{baseline['id']}").json()
    assert (
        completed["prediction"]["sampled_timestamps"]
        == baseline_result["prediction"]["sampled_timestamps"]
    )
    assert completed["configuration"] == edited["configuration"]


@pytest.mark.parametrize(
    "experiment",
    [
        {"model": "not-served"},
        {"prompt_text": ""},
        {"prompt_text": " \n "},
        {"prompt_text": "x" * 16001},
        {"generation": {"temperature": "NaN", "max_tokens": 32}},
        {"generation": {"temperature": -0.1, "max_tokens": 32}},
        {"generation": {"temperature": 2.1, "max_tokens": 32}},
        {"generation": {"temperature": 0, "max_tokens": 0}},
        {"generation": {"temperature": 0, "max_tokens": 1}},
        {"generation": {"temperature": 0, "max_tokens": 15}},
        {"generation": {"temperature": 0, "max_tokens": 4097}},
        {"generation": {"temperature": 0, "max_tokens": 1.5}},
    ],
)
def test_api_rejects_invalid_experiments_without_queuing(client, api_instance, experiment):
    _, repository, _ = api_instance
    video = client.post("/videos/sample").json()
    response = client.post("/analysis-jobs", json={"video_id": video["id"], **experiment})
    assert response.status_code == 422
    assert repository.list_jobs() == []


def test_new_minimum_token_budget_preserves_historical_configuration(client, api_instance):
    _, repository, _ = api_instance
    video = client.post("/videos/sample").json()
    historical = repository.create_job(
        video["id"], 0, 2, generation={"temperature": 0, "max_tokens": 1}
    )
    assert (
        client.get(f"/analysis-jobs/{historical.id}").json()["configuration"]["generation"][
            "max_tokens"
        ]
        == 1
    )
    response = client.post(
        "/analysis-jobs",
        json={"video_id": video["id"], "generation": {"temperature": 0, "max_tokens": 16}},
    )
    assert response.status_code == 202
    assert response.json()["configuration"]["generation"]["max_tokens"] == 16
    assert client.get("/capabilities").json()["generation_limits"]["min_max_tokens"] == 16


def test_app_instances_keep_settings_and_storage_isolated(make_api):
    first_app, first_repository, first_data_dir = make_api("first", inference_model="model-a")
    second_app, second_repository, second_data_dir = make_api("second", inference_model="model-b")
    assert not first_data_dir.exists()
    assert not second_data_dir.exists()

    with TestClient(first_app) as first_client, TestClient(second_app) as second_client:
        assert first_client.get("/capabilities").json()["models"] == ["model-a"]
        assert second_client.get("/capabilities").json()["models"] == ["model-b"]
        uploaded = first_client.post("/videos", files={"file": ("clip.mp4", b"video", "video/mp4")})
        assert uploaded.status_code == 201
        video_id = uploaded.json()["id"]
        assert first_client.get(f"/videos/{video_id}").status_code == 200
        assert second_client.get(f"/videos/{video_id}").status_code == 404
        assert second_client.post("/videos/sample").status_code == 200

    assert first_repository.get_video(video_id) is not None
    assert second_repository.get_video(video_id) is None
    assert len(list((first_data_dir / "media").iterdir())) == 1
    assert not (second_data_dir / "media").exists()
    assert first_data_dir.joinpath("app.sqlite3").exists()
    assert second_data_dir.joinpath("app.sqlite3").exists()


@pytest.mark.parametrize("value", ["Infinity", "NaN", "-Infinity"])
def test_api_rejects_non_finite_timestamps(client, value):
    response = client.post("/analysis-jobs", json={"video_id": "unused", "end_seconds": value})
    assert response.status_code == 422


def test_upload_limit_and_failed_metadata_leave_no_orphans(make_api, monkeypatch):
    small_app, _, small_data_dir = make_api("small", upload_max_bytes=3)
    with TestClient(small_app) as client:
        oversized = client.post("/videos", files={"file": ("clip.mp4", b"1234", "video/mp4")})
    assert oversized.status_code == 413
    assert list((small_data_dir / "media").iterdir()) == []

    app, repository, data_dir = make_api("metadata", upload_max_bytes=100)

    def fail_metadata(*_args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "create_uploaded_video", fail_metadata)
    with TestClient(app) as client, pytest.raises(RuntimeError, match="database unavailable"):
        client.post("/videos", files={"file": ("clip.mp4", b"123", "video/mp4")})
    assert list((data_dir / "media").iterdir()) == []


def _multipart_body(data: bytes) -> bytes:
    return (
        b"--clip-boundary\r\n"
        b'Content-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
        b"Content-Type: video/mp4\r\n\r\n" + data + b"\r\n--clip-boundary--\r\n"
    )


def _stream_upload(
    app, chunks: list[bytes], headers: list[tuple[bytes, bytes]], *, disconnect=False
):
    pending = iter(chunks)
    received = 0
    sent = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/videos",
        "raw_path": b"/videos",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"multipart/form-data; boundary=clip-boundary"), *headers],
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
    }

    async def receive():
        nonlocal received
        try:
            chunk = next(pending)
        except StopIteration:
            return {"type": "http.disconnect"}
        received += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": disconnect or received < len(chunks),
        }

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent, received


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
def test_upload_ingress_limit_counts_streamed_bytes_without_trusting_length(make_api, headers):
    app, _, data_dir = make_api(upload_max_bytes=8)
    body = _multipart_body(b"x" * MULTIPART_OVERHEAD_BYTES)
    with TestClient(app):
        messages, received = _stream_upload(app, [body[:1024], body[1024:], b"unread"], headers)

    assert messages[0]["status"] == 413
    assert received == 2
    assert not (data_dir / "media").exists()


def test_upload_ingress_limit_allows_valid_file_despite_high_length_header(make_api):
    app, _, data_dir = make_api(upload_max_bytes=4)
    body = _multipart_body(b"1234")
    with TestClient(app):
        messages, received = _stream_upload(
            app, [body[:80], body[80:]], [(b"content-length", b"999999")]
        )

    assert messages[0]["status"] == 201
    assert received == 2
    assert len(list((data_dir / "media").iterdir())) == 1


def test_interrupted_upload_leaves_no_persistent_media(make_api):
    app, _, data_dir = make_api()
    body = _multipart_body(b"partial video")
    with TestClient(app):
        messages, received = _stream_upload(app, [body[:80]], [], disconnect=True)

    assert messages[0]["status"] == 400
    assert received == 1
    assert not (data_dir / "media").exists()
