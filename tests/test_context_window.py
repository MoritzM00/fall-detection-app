"""Configurations that cannot fit the served context window are rejected at submission."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.request_guards import CLIENT_HEADERS
from fall_detection.config import Settings
from fall_detection.models import (
    ContextWindowError,
    check_context_window,
    estimate_request_tokens,
)
from fall_detection.prediction import THESIS_BASELINE_PROMPT, InferenceClient
from fall_detection.repository import Repository

DEPLOYED_MAX_MODEL_LEN = 5120  # deploy/modal_vllm.py --max-model-len
LOCAL_API = "http://127.0.0.1"


@pytest.mark.parametrize(
    ("frames", "size", "visual"),
    # Issue #77 table: 16 × 448 ≈ 1.6k, 16 × 672 ≈ 3.5k, 32 × 576 ≈ 5.2k, 32 × 672 ≈ 7.1k.
    [(16, 448, 8 * 196), (16, 672, 8 * 441), (32, 576, 16 * 324), (32, 672, 16 * 441)],
)
def test_estimate_bounds_qwen3_vl_visual_tokens_from_above(frames, size, visual):
    estimate = estimate_request_tokens(frames, size, "", 0)
    assert visual < estimate <= visual + frames * 8 + 32


def test_estimate_counts_odd_frames_small_crops_prompt_bytes_and_answer_budget():
    assert estimate_request_tokens(3, 448, "", 0) == estimate_request_tokens(4, 448, "", 0)
    assert estimate_request_tokens(16, 224, "", 0) > 8 * 7 * 7
    base = estimate_request_tokens(16, 448, "", 0)
    assert estimate_request_tokens(16, 448, "ä", 100) == base + 2 + 100


@pytest.mark.parametrize(
    ("frames", "size", "max_tokens", "fits"),
    [
        (16, 448, 32, True),  # thesis baseline
        (16, 672, 32, True),  # validated live on the L4 deployment
        (32, 448, 32, True),
        (32, 576, 32, False),
        (32, 672, 32, False),
        (16, 448, 2985, True),  # largest answer budget at the baseline (docs)
        (16, 448, 2986, False),
        (16, 448, 4096, False),
        (2, 224, 4096, True),  # the static 4096 cap still fits the smallest window
    ],
)
def test_deployed_context_window_accepts_known_fits_and_rejects_overflows(
    frames, size, max_tokens, fits
):
    def check():
        check_context_window(
            frames, size, THESIS_BASELINE_PROMPT, max_tokens, DEPLOYED_MAX_MODEL_LEN
        )

    if fits:
        check()
    else:
        with pytest.raises(ContextWindowError, match="context window is 5120 tokens"):
            check()


def test_unset_context_window_skips_the_check():
    check_context_window(32, 672, THESIS_BASELINE_PROMPT, 4096, None)


def test_max_model_len_setting_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("FALL_DETECTION_MAX_MODEL_LEN", "5120")
    assert Settings.from_env().max_model_len == 5120
    monkeypatch.setenv("FALL_DETECTION_MAX_MODEL_LEN", "")
    assert Settings.from_env().max_model_len is None
    monkeypatch.setenv("FALL_DETECTION_MAX_MODEL_LEN", "0")
    with pytest.raises(ValueError, match="positive"):
        Settings.from_env()


@pytest.fixture
def served(tmp_path, monkeypatch):
    """An API with the deployed context window that fails on any serving request."""

    def no_serving(*_args, **_kwargs):
        raise AssertionError("rejected configurations must not reach serving")

    monkeypatch.setattr(InferenceClient, "discover_mock_identity", no_serving)
    monkeypatch.setattr(InferenceClient, "complete", no_serving)
    settings = replace(
        Settings.from_env(),
        data_dir=tmp_path,
        database_path=tmp_path / "app.sqlite3",
        max_model_len=DEPLOYED_MAX_MODEL_LEN,
    )
    repository = Repository(settings.database_path)
    with TestClient(
        create_app(settings, repository), base_url=LOCAL_API, headers=CLIENT_HEADERS
    ) as client:
        yield client, repository


OVER_LIMIT = [
    {"frame_count": 32, "size": 672},
    {"generation": {"temperature": 0, "max_tokens": 4096}},
]


def test_capabilities_report_the_context_window(served):
    client, _ = served
    limits = client.get("/capabilities").json()["serving_limits"]
    assert limits == {"max_model_len": DEPLOYED_MAX_MODEL_LEN}


@pytest.mark.parametrize("overrides", OVER_LIMIT)
def test_clip_job_over_the_context_window_gets_422_before_queueing(served, overrides):
    client, repository = served
    video = client.post("/videos/sample").json()
    response = client.post("/analysis-jobs", json={"video_id": video["id"], **overrides})
    assert response.status_code == 422
    assert "context window is 5120 tokens" in response.json()["detail"]
    assert repository.list_jobs() == []


@pytest.mark.parametrize("overrides", OVER_LIMIT)
def test_monitoring_sessions_over_the_context_window_get_422(served, overrides):
    client, _ = served
    video = client.post("/videos/sample").json()
    replay = {"video_id": video["id"], "duration_seconds": video["duration_seconds"], **overrides}
    response = client.post("/monitoring-sessions", json=replay)
    assert response.status_code == 422
    assert "context window" in response.json()["detail"]
    live = client.post("/monitoring-sessions/live", json=overrides)
    assert live.status_code == 422
    assert "context window" in live.json()["detail"]
    assert client.get("/monitoring-sessions").json() == []


@pytest.mark.parametrize("overrides", OVER_LIMIT)
def test_configure_over_the_context_window_gets_422_and_keeps_the_segment(
    served, monkeypatch, overrides
):
    client, _ = served
    monkeypatch.setattr(InferenceClient, "discover_mock_identity", lambda self: "sample-v1")
    video = client.post("/videos/sample").json()
    session = client.post(
        "/monitoring-sessions",
        json={"video_id": video["id"], "duration_seconds": video["duration_seconds"]},
    ).json()
    url = f"/monitoring-sessions/{session['id']}/commands"
    configuration = {"video_id": video["id"], **overrides}
    response = client.post(url, json={"action": "configure", "configuration": configuration})
    assert response.status_code == 422
    assert "context window" in response.json()["detail"]
    after = client.get(f"/monitoring-sessions/{session['id']}").json()
    assert after["segment_id"] == session["segment_id"]


def test_clip_job_within_the_context_window_is_queued(served, monkeypatch):
    client, repository = served
    monkeypatch.setattr(InferenceClient, "discover_mock_identity", lambda self: "sample-v1")
    video = client.post("/videos/sample").json()
    request = {"video_id": video["id"], "frame_count": 16, "size": 672, "end_seconds": 2}
    assert client.post("/analysis-jobs", json=request).status_code == 202
    assert len(repository.list_jobs()) == 1
