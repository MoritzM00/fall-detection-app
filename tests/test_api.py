import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import apps.api.main as api
from fall_detection.repository import Repository
from fall_detection.upload_limit import MULTIPART_OVERHEAD_BYTES


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = replace(
        api.settings,
        data_dir=tmp_path,
        database_path=tmp_path / "app.sqlite3",
        inference_model="custom-model",
    )
    repository = Repository(settings.database_path)
    monkeypatch.setattr(api, "settings", settings)
    monkeypatch.setattr(api, "repository", repository)
    with TestClient(api.app) as client:
        yield client


def test_api_snapshots_configured_model(client):
    video = client.post("/videos/sample").json()
    response = client.post("/analysis-jobs", json={"video_id": video["id"]})
    assert response.status_code == 202
    model, _ = api.repository.get_inference_configuration(response.json()["configuration_id"])
    assert model == "custom-model"


@pytest.mark.parametrize("value", ["Infinity", "NaN", "-Infinity"])
def test_api_rejects_non_finite_timestamps(client, value):
    response = client.post("/analysis-jobs", json={"video_id": "unused", "end_seconds": value})
    assert response.status_code == 422


def test_upload_limit_and_failed_metadata_leave_no_orphans(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "settings", replace(api.settings, upload_max_bytes=3))
    oversized = client.post("/videos", files={"file": ("clip.mp4", b"1234", "video/mp4")})
    assert oversized.status_code == 413
    assert list((tmp_path / "media").iterdir()) == []

    monkeypatch.setattr(api, "settings", replace(api.settings, upload_max_bytes=100))

    def fail_metadata(*_args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(api.repository, "create_uploaded_video", fail_metadata)
    with pytest.raises(RuntimeError, match="database unavailable"):
        client.post("/videos", files={"file": ("clip.mp4", b"123", "video/mp4")})
    assert list((tmp_path / "media").iterdir()) == []


def _multipart_body(data: bytes) -> bytes:
    return (
        b"--clip-boundary\r\n"
        b'Content-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
        b"Content-Type: video/mp4\r\n\r\n" + data + b"\r\n--clip-boundary--\r\n"
    )


def _stream_upload(
    chunks: list[bytes], headers: list[tuple[bytes, bytes]], *, disconnect: bool = False
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

    asyncio.run(api.app(scope, receive, send))
    return sent, received


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
def test_upload_ingress_limit_counts_streamed_bytes_without_trusting_length(
    client, tmp_path, monkeypatch, headers
):
    monkeypatch.setattr(api, "settings", replace(api.settings, upload_max_bytes=8))
    body = _multipart_body(b"x" * MULTIPART_OVERHEAD_BYTES)
    messages, received = _stream_upload([body[:1024], body[1024:], b"unread"], headers)

    assert messages[0]["status"] == 413
    assert received == 2
    assert not (tmp_path / "media").exists()


def test_upload_ingress_limit_allows_valid_file_despite_high_length_header(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(api, "settings", replace(api.settings, upload_max_bytes=4))
    body = _multipart_body(b"1234")
    messages, received = _stream_upload([body[:80], body[80:]], [(b"content-length", b"999999")])

    assert messages[0]["status"] == 201
    assert received == 2
    assert len(list((tmp_path / "media").iterdir())) == 1


def test_interrupted_upload_leaves_no_persistent_media(client, tmp_path):
    body = _multipart_body(b"partial video")

    messages, received = _stream_upload([body[:80]], [], disconnect=True)

    assert messages[0]["status"] == 400
    assert received == 1
    assert not (tmp_path / "media").exists()
