import httpx
import pytest

from fall_detection.config import Settings
from scripts import warm_gpu


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("FALL_DETECTION_BACKEND_KIND", "vllm")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_BASE_URL", "https://gpu.test/v1")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_KEY", "wk-id")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_SECRET", "ws-secret")
    return Settings.from_env()


def test_warm_polls_health_with_credentials_until_ready(monkeypatch, settings):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs["headers"], kwargs["follow_redirects"]))
        if len(calls) == 1:
            raise httpx.ConnectError("starting")
        return httpx.Response(200 if len(calls) == 3 else 503)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(warm_gpu.time, "sleep", lambda _seconds: None)
    warm_gpu.warm(settings)
    assert len(calls) == 3
    assert calls[0] == (
        "https://gpu.test/health",
        {"Modal-Key": "wk-id", "Modal-Secret": "ws-secret"},
        True,
    )


def test_warm_stops_on_rejected_credentials(monkeypatch, settings):
    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: httpx.Response(401))
    with pytest.raises(PermissionError, match="401"):
        warm_gpu.warm(settings)


def test_warm_refuses_the_mock_backend(monkeypatch):
    monkeypatch.setenv("FALL_DETECTION_BACKEND_KIND", "mock")
    with pytest.raises(ValueError, match="vllm"):
        warm_gpu.warm(Settings.from_env())
