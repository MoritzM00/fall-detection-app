import httpx
import pytest

from fall_detection.config import Settings
from fall_detection.inference import InferenceClient, InferenceServiceError


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="unavailable"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"id": "x", "choices": []}),
        httpx.Response(200, json={"id": "x", "choices": [{"message": {"content": ["not text"]}}]}),
    ],
)
def test_inference_boundary_rejects_http_and_envelope_errors(monkeypatch, response):
    response.request = httpx.Request("POST", "http://test/chat/completions")
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: response)
    with pytest.raises(InferenceServiceError, match="inference"):
        InferenceClient("http://test").complete({})


def test_inference_boundary_reports_timeout(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "post", timeout)
    with pytest.raises(InferenceServiceError, match="timed out"):
        InferenceClient("http://test").complete({})


def test_inference_client_sends_configured_credentials(monkeypatch):
    sent = {}

    def post(url, **kwargs):
        sent.update(kwargs["headers"])
        response = httpx.Response(
            200, json={"id": "x", "choices": [{"message": {"content": "fall"}}]}
        )
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setattr(httpx, "post", post)
    InferenceClient("http://test", auth_headers={"Authorization": "Bearer k"}).complete({})
    assert sent == {"Authorization": "Bearer k"}


def test_settings_build_auth_headers_and_hide_secrets(monkeypatch):
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_API_KEY", "api-secret")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_KEY", "wk-id")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_SECRET", "ws-secret")
    settings = Settings.from_env()
    assert settings.inference_auth_headers() == {
        "Authorization": "Bearer api-secret",
        "Modal-Key": "wk-id",
        "Modal-Secret": "ws-secret",
    }
    assert "secret" not in repr(settings)


def test_settings_reject_half_configured_proxy_token(monkeypatch):
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_KEY", "wk-id")
    monkeypatch.delenv("FALL_DETECTION_INFERENCE_PROXY_SECRET", raising=False)
    with pytest.raises(ValueError, match="together"):
        Settings.from_env().inference_auth_headers()
