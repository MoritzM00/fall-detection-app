import httpx
import pytest

from fall_detection.config import Settings
from fall_detection.prediction import (
    ACTIVITY_LABELS,
    InferenceClient,
    InferenceServiceError,
    PredictionParseError,
    parse_activity_label,
    require_encrypted_transport,
    sample_timestamps,
)


@pytest.mark.parametrize("label", ACTIVITY_LABELS)
def test_parser_accepts_every_canonical_label(label: str) -> None:
    assert parse_activity_label(f"The best answer is: {label}") == label


@pytest.mark.parametrize("label", ACTIVITY_LABELS)
def test_custom_parser_accepts_single_canonical_labels_without_relaxing_baseline(label):
    assert parse_activity_label(f" \n{label}\n", allow_bare_label=True) == label
    assert parse_activity_label(f"The best answer is: {label}", allow_bare_label=True) == label
    with pytest.raises(PredictionParseError):
        parse_activity_label(label)


@pytest.mark.parametrize("response", ["unknown", "walk or fall", '{"label":"walk"}', "walk."])
def test_custom_parser_rejects_ambiguous_or_unsupported_formats(response):
    with pytest.raises(PredictionParseError):
        parse_activity_label(response, allow_bare_label=True)


@pytest.mark.parametrize(
    "response",
    [
        "fall",
        "The best answer is: unknown",
        "The best answer is: fall or fallen",
        "The best answer is: ",
    ],
)
def test_parser_rejects_invalid_output_instead_of_using_other(response: str) -> None:
    with pytest.raises(PredictionParseError):
        parse_activity_label(response)


def test_sample_timestamps_preserve_window_edges() -> None:
    timestamps = sample_timestamps(4.0, 6.0)
    assert len(timestamps) == 16
    assert timestamps[0] == 4.0
    assert timestamps[-1] == 6.0


def test_sample_timestamps_reject_invalid_window() -> None:
    with pytest.raises(ValueError):
        sample_timestamps(2.0, 2.0)


@pytest.mark.parametrize("start,end", [(-1, 2), (0, float("inf")), (float("nan"), 2)])
def test_sample_timestamps_reject_invalid_numbers(start, end) -> None:
    with pytest.raises(ValueError):
        sample_timestamps(start, end)


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
        assert kwargs["follow_redirects"] is True
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


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens": 1700, "completion_tokens": 6}, (1700, 6)),
        (None, (None, None)),
        ({"prompt_tokens": "1700", "completion_tokens": True}, (None, None)),
        ({"prompt_tokens": -1}, (None, None)),
    ],
)
def test_inference_client_reads_optional_token_usage(monkeypatch, usage, expected):
    body = {"id": "x", "choices": [{"message": {"content": "fall"}}]}
    if usage is not None:
        body["usage"] = usage
    response = httpx.Response(200, json=body)
    response.request = httpx.Request("POST", "http://test/chat/completions")
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: response)
    result = InferenceClient("http://test").complete({})
    assert (result.prompt_tokens, result.completion_tokens) == expected


@pytest.mark.parametrize(
    "base_url",
    ["https://gpu.example/v1", "http://127.0.0.1:8001/v1", "http://localhost:8001/v1"],
)
def test_encrypted_or_local_transport_is_allowed(base_url):
    require_encrypted_transport(base_url)


@pytest.mark.parametrize(
    "base_url", ["http://gpu.example/v1", "http://10.0.0.5:8000/v1", "ftp://gpu.example/v1"]
)
def test_plaintext_transport_off_this_machine_is_refused(base_url):
    with pytest.raises(InferenceServiceError, match="Refusing to send video frames"):
        require_encrypted_transport(base_url)
