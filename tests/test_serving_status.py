import subprocess
import threading
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from fall_detection import serving_status
from fall_detection.config import Settings
from fall_detection.prediction import InferenceServiceError
from fall_detection.serving_status import (
    ContainerStats,
    ServingStatus,
    ShutdownBlockedError,
    modal_stop_containers,
    wait_until_healthy,
)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("FALL_DETECTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FALL_DETECTION_BACKEND_KIND", "vllm")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_BASE_URL", "https://gpu.test/v1")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_KEY", "wk-id")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_SECRET", "ws-secret")
    monkeypatch.setenv("FALL_DETECTION_MODAL_APP", "fall-detection-vllm")
    return Settings.from_env()


def test_wait_polls_health_with_credentials_until_ready(settings):
    calls = []

    def handler(request):
        calls.append((str(request.url), request.headers.get("modal-key")))
        if len(calls) == 1:
            raise httpx.ConnectError("starting")
        return httpx.Response(200 if len(calls) == 3 else 503)

    wait_until_healthy(
        settings, sleep=lambda _seconds: None, transport=httpx.MockTransport(handler)
    )
    assert calls == [("https://gpu.test/health", "wk-id")] * 3


def test_wait_stops_on_rejected_credentials(settings):
    transport = httpx.MockTransport(lambda _request: httpx.Response(401))
    with pytest.raises(PermissionError, match="401"):
        wait_until_healthy(settings, sleep=lambda _seconds: None, transport=transport)


def test_wait_follows_a_same_origin_303(settings):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if not request.url.query:
            return httpx.Response(303, headers={"Location": "/health?poll=dummy"})
        return httpx.Response(200)

    wait_until_healthy(
        settings, sleep=lambda _seconds: None, transport=httpx.MockTransport(handler)
    )
    assert seen == ["https://gpu.test/health", "https://gpu.test/health?poll=dummy"]


@pytest.mark.parametrize("location", ["https://elsewhere.test/health", "http://gpu.test/health"])
def test_wait_refuses_redirects_off_the_configured_origin(settings, location):
    foreign = []

    def handler(request):
        if request.url.host != "gpu.test" or request.url.scheme != "https":
            foreign.append(request)
            return httpx.Response(200)
        return httpx.Response(307, headers={"Location": location})

    with pytest.raises(InferenceServiceError, match="Refusing redirect") as refused:
        wait_until_healthy(
            settings, sleep=lambda _seconds: None, transport=httpx.MockTransport(handler)
        )
    assert foreign == []
    assert "wk-id" not in str(refused.value) and "ws-secret" not in str(refused.value)


def test_wait_refuses_the_mock_backend(settings):
    with pytest.raises(ValueError, match="vllm"):
        wait_until_healthy(replace(settings, backend_kind="mock"))


def test_status_reads_containers_without_contacting_vllm(monkeypatch, route_inference, settings):
    def no_vllm_requests(*_args, **_kwargs):
        raise AssertionError("status must not contact the serving endpoint")

    monkeypatch.setattr(httpx, "get", no_vllm_requests)
    route_inference(no_vllm_requests)
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


def test_shutdown_stops_containers_and_clears_readiness(settings):
    containers = [1]
    stopped = []

    def stop(app_name):
        stopped.append(app_name)
        containers[0] = 0
        return 1

    status = ServingStatus(
        settings,
        stats_reader=lambda _app: ContainerStats(containers=containers[0], backlog=0),
        warm=lambda _settings: 3.0,
        stop=stop,
        cache_seconds=0,
    )
    status._run_warmup()
    assert status.snapshot()["state"] == "ready"
    snapshot = status.shutdown()
    assert stopped == ["fall-detection-vllm"]
    assert snapshot["state"] == "idle"
    assert snapshot["warmup"]["state"] == "idle"


@pytest.mark.parametrize(("running", "backlog"), [(1, 0), (0, 2)])
def test_shutdown_refuses_while_requests_are_in_flight(settings, running, backlog):
    stopped = []
    status = ServingStatus(
        settings,
        stats_reader=lambda _app: ContainerStats(
            containers=1, backlog=backlog, running_inputs=running
        ),
        stop=lambda app_name: stopped.append(app_name) or 0,
    )
    with pytest.raises(ShutdownBlockedError, match="in flight"):
        status.shutdown()
    assert stopped == []


def test_shutdown_needs_a_monitored_vllm_backend(settings):
    with pytest.raises(ValueError, match="FALL_DETECTION_MODAL_APP"):
        ServingStatus(replace(settings, modal_app=None)).shutdown()


def test_modal_stop_uses_the_cli_for_this_app_only(monkeypatch):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command[3:])
        listing = (
            '[{"container_id": "ta-1", "app_name": "fall-detection-vllm"},'
            ' {"container_id": "ta-2", "app_name": "other-app"}]'
        )
        return subprocess.CompletedProcess(command, 0, stdout=listing if "list" in command else "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert modal_stop_containers("fall-detection-vllm") == 1
    assert calls == [["container", "list", "--json"], ["container", "stop", "--yes", "ta-1"]]


def test_api_maps_blocked_shutdown_to_conflict(settings, monkeypatch):
    def blocked(_self):
        raise ShutdownBlockedError("1 request(s) in flight")

    monkeypatch.setattr(ServingStatus, "shutdown", blocked)
    with TestClient(create_app(settings)) as client:
        response = client.post("/serving-status/shutdown")
    assert response.status_code == 409
    assert "in flight" in response.json()["detail"]


def test_capabilities_name_the_inference_host_without_credentials(settings):
    remote = replace(settings, inference_base_url="https://user:pw@gpu.modal.run/v1")
    with TestClient(create_app(remote)) as client:
        body = client.get("/capabilities").json()
    assert (body["inference_host"], body["inference_encrypted"]) == ("gpu.modal.run", True)
    assert "pw" not in str(body)
    with TestClient(create_app(replace(settings, backend_kind="mock"))) as client:
        assert client.get("/capabilities").json()["inference_host"] is None
