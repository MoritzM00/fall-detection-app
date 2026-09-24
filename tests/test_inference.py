import httpx
import pytest

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
