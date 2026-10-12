import httpx
import pytest

from fall_detection.prediction import (
    ACTIVITY_LABELS,
    InferenceClient,
    InferenceServiceError,
    PredictionParseError,
    parse_activity_label,
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
