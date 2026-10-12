import threading
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from fall_detection import serving_status
from fall_detection.config import Settings
from fall_detection.serving_status import ContainerStats, ServingStatus, wait_until_healthy


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("FALL_DETECTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FALL_DETECTION_BACKEND_KIND", "vllm")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_BASE_URL", "https://gpu.test/v1")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_KEY", "wk-id")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_SECRET", "ws-secret")
    monkeypatch.setenv("FALL_DETECTION_MODAL_APP", "fall-detection-vllm")
    return Settings.from_env()


def test_wait_polls_health_with_credentials_until_ready(monkeypatch, settings):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs["headers"], kwargs["follow_redirects"]))
        if len(calls) == 1:
            raise httpx.ConnectError("starting")
        return httpx.Response(200 if len(calls) == 3 else 503)

    monkeypatch.setattr(httpx, "get", fake_get)
    wait_until_healthy(settings, sleep=lambda _seconds: None)
    assert len(calls) == 3
    assert calls[0] == (
        "https://gpu.test/health",
        {"Modal-Key": "wk-id", "Modal-Secret": "ws-secret"},
        True,
    )


def test_wait_stops_on_rejected_credentials(monkeypatch, settings):
    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: httpx.Response(401))
    with pytest.raises(PermissionError, match="401"):
        wait_until_healthy(settings, sleep=lambda _seconds: None)


def test_wait_refuses_the_mock_backend(settings):
    with pytest.raises(ValueError, match="vllm"):
        wait_until_healthy(replace(settings, backend_kind="mock"))


def test_status_reads_containers_without_contacting_vllm(monkeypatch, settings):
    def no_vllm_requests(*_args, **_kwargs):
        raise AssertionError("status must not contact the serving endpoint")

    monkeypatch.setattr(httpx, "get", no_vllm_requests)
    reads = []
    counts = iter([0, 1])

    def stats(app_name):
        reads.append(app_name)
        return ContainerStats(containers=next(counts), backlog=0)

    status = ServingStatus(settings, stats_reader=stats, cache_seconds=0)
    assert status.snapshot()["state"] == "idle"
    assert status.snapshot()["state"] == "running"
    assert reads == ["fall-detection-vllm", "fall-detection-vllm"]


def test_status_caches_container_reads(settings):
    reads = []

    def stats(_app_name):
        reads.append(1)
        return ContainerStats(containers=0, backlog=0)

    status = ServingStatus(settings, stats_reader=stats, cache_seconds=60)
    status.snapshot()
    status.snapshot()
    assert len(reads) == 1


def test_warmup_reports_warming_then_ready_until_scaled_down(settings):
    release = threading.Event()
    containers = [1]

    def warm(_settings):
        release.wait(timeout=5)
        return 42.0

    status = ServingStatus(
        settings,
        stats_reader=lambda _app: ContainerStats(containers=containers[0], backlog=0),
        warm=warm,
        cache_seconds=0,
    )
    assert status.start_warmup()["state"] == "warming"
    assert status.start_warmup()["warmup"]["state"] == "warming"
    release.set()
    deadline = time.monotonic() + 5
    while status.snapshot()["warmup"]["state"] == "warming" and time.monotonic() < deadline:
        time.sleep(0.01)
    ready = status.snapshot()
    assert ready["state"] == "ready"
    assert ready["warmup"] == {"state": "ready", "seconds": 42.0, "error": None}
    containers[0] = 0
    # Within the grace period a zero count is treated as control-plane lag.
    assert status.snapshot()["state"] == "ready"
    status._ready_at -= serving_status.READY_GRACE_SECONDS + 1
    assert status.snapshot()["state"] == "idle"
    assert status.snapshot()["warmup"]["state"] == "idle"


def test_failed_warmup_and_unreadable_stats_are_explicit(settings):
    def broken_stats(_app):
        raise RuntimeError("no Modal token")

    def failing_warm(_settings):
        raise PermissionError("endpoint rejected credentials (401)")

    status = ServingStatus(settings, stats_reader=broken_stats, warm=failing_warm)
    status._run_warmup()
    snapshot = status.snapshot()
    assert snapshot["state"] == "unknown"
    assert snapshot["detail"] == "Container status unavailable: RuntimeError"
    assert snapshot["warmup"]["state"] == "failed"
    assert "401" in (snapshot["warmup"]["error"] or "")


def test_status_without_modal_app_is_unmonitored(settings):
    status = ServingStatus(replace(settings, modal_app=None))
    assert status.snapshot()["state"] == "unmonitored"


def test_api_reports_simulated_status_and_refuses_mock_warmup(settings):
    with TestClient(create_app(replace(settings, backend_kind="mock"))) as client:
        assert client.get("/serving-status").json()["state"] == "simulated"
        assert client.post("/serving-status/warmup").status_code == 409
