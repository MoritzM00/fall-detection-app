"""Disposable CPU acceptance through real API, worker and simulated HTTP serving."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from scripts.dev import stop_processes
from scripts.validate_vllm_contract import _generate_clip

ROOT = Path(__file__).resolve().parents[1]


class Stack:
    """Own isolated sockets and children; never discover or reuse another service."""

    def __init__(self, directory: Path, fault: str = "none") -> None:
        """Build a fixture environment without inheriting application overrides."""
        self.directory = directory
        self.children: list[subprocess.Popen] = []
        self.sockets: list[socket.socket] = []
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("FALL_DETECTION_", "MOCK_INFERENCE_"))
        }
        manifest = directory / "manifest.json"
        manifest.write_text(
            json.dumps(
                {"default": {"delay_ms": 0, "fault": fault, "fail_first": int(fault != "none")}}
            ),
            encoding="utf-8",
        )
        self.environment.update(
            FALL_DETECTION_DATA_DIR=str(directory / "data"),
            FALL_DETECTION_DATABASE_PATH=str(directory / "data" / "app.sqlite3"),
            FALL_DETECTION_BACKEND_KIND="mock",
            FALL_DETECTION_REQUEST_TIMEOUT_SECONDS="1",
            MOCK_INFERENCE_MANIFEST=str(manifest),
            MOCK_INFERENCE_ATTEMPT_DB=str(directory / "attempts.sqlite3"),
        )

    def server(
        self, module: str, listener: socket.socket | None = None
    ) -> tuple[subprocess.Popen, socket.socket, str]:
        """Pass a bound ephemeral socket directly to uvicorn, avoiding bind races."""
        if listener is None:
            listener = socket.socket()
            self.sockets.append(listener)
            listener.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        process = self.spawn(
            ["-m", "uvicorn", module, "--fd", str(listener.fileno()), "--log-level", "error"],
            (listener.fileno(),),
        )
        path = "/v1/mock/identity" if "mock_inference" in module else "/health"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{module} exited during startup: {process.returncode}")
            try:
                if httpx.get(url + path, timeout=0.2, trust_env=False).status_code == 200:
                    return process, listener, url
            except httpx.RequestError:
                pass
            time.sleep(0.05)
        raise RuntimeError(f"{module} did not become ready")

    def spawn(self, arguments: list[str], descriptors: tuple[int, ...] = ()) -> subprocess.Popen:
        """Start only a directly owned child with bounded cleanup."""
        process = subprocess.Popen(
            [sys.executable, *arguments],
            cwd=ROOT,
            env=self.environment,
            pass_fds=descriptors,
            stdout=subprocess.DEVNULL,
        )
        self.children.append(process)
        return process

    def close(self) -> None:
        """Reap children before closing their reserved sockets."""
        try:
            stop_processes(self.children, timeout_seconds=3)
        finally:
            for listener in self.sockets:
                listener.close()


def wait_job(client: httpx.Client, job_id: str) -> dict:
    """Wait a bounded time for an actual durable worker result."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f"/analysis-jobs/{job_id}").raise_for_status().json()
        if job["state"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.05)
    raise RuntimeError("Worker did not finish the demo job within 15 seconds")


def scenario(name: str, directory: Path) -> dict:
    """Exercise generated media, HTTP faults, explicit retry and API recovery."""
    stack = Stack(directory, fault="429" if name == "overloaded" else "none")
    try:
        mock, _, endpoint = stack.server("apps.mock_inference.main:app")
        stack.environment["FALL_DETECTION_INFERENCE_BASE_URL"] = endpoint + "/v1"
        api, api_socket, api_url = stack.server("apps.api.main:app")
        with httpx.Client(base_url=api_url, timeout=10, trust_env=False) as client:
            clip = directory / "generated" / "clip.mp4"
            _generate_clip(clip)
            with clip.open("rb") as media:
                video = (
                    client.post("/videos", files={"file": ("generated.mp4", media, "video/mp4")})
                    .raise_for_status()
                    .json()
                )
            settings = {
                "video_id": video["id"],
                "start_seconds": 0,
                "frame_count": 6,
                "fps": 5,
                "size": 224,
            }
            prepared = client.post("/prepared-inputs", json=settings).raise_for_status().json()
            job = (
                client.post(
                    "/analysis-jobs",
                    json={**settings, "end_seconds": 1, "prepared_input_id": prepared["id"]},
                )
                .raise_for_status()
                .json()
            )
            if name == "disconnected":
                stop_processes([mock], timeout_seconds=3)
            worker = stack.spawn(["-m", "apps.worker.main"])
            result = wait_job(client, job["id"])
            if name in {"overloaded", "disconnected"}:
                assert result["state"] == "failed" and result["prediction"] is None, result
                if name == "disconnected":
                    # The parent owns the socket throughout the outage/restart.
                    stack.server("apps.mock_inference.main:app", stack.sockets[0])
                retried = client.post(f"/analysis-jobs/{job['id']}/retry").raise_for_status().json()
                assert retried["configuration_id"] == job["configuration_id"]
                result = wait_job(client, job["id"])
            assert result["state"] == "succeeded", result
            prediction = result["prediction"]
            assert prediction["label"] == "fall" and prediction["backend_kind"] == "mock"
            assert prediction["sampled_timestamps"] == [
                frame["actual_seconds"] for frame in prepared["frames"]
            ]
            assert result["configuration_id"] == job["configuration_id"]
            assert result["prepared_input_id"] == prepared["id"]
            assert prediction["fixture_version"] == result["configuration"]["fixture_version"]
            exported = client.get(f"/analysis-jobs/{job['id']}/export").raise_for_status().json()
            exported_run = exported["runs"][0]
            assert exported_run["provenance"] == "simulated"
            assert exported_run["configuration_id"] == job["configuration_id"]
            assert (
                exported_run["prediction"]["sampled_timestamps"] == prediction["sampled_timestamps"]
            )
            coverage = []
            if name == "overloaded":
                stop_processes([worker], timeout_seconds=3)
                session = (
                    client.post(
                        "/monitoring-sessions",
                        json={**settings, "duration_seconds": 3, "stride_seconds": 0.25},
                    )
                    .raise_for_status()
                    .json()
                )
                path = f"/monitoring-sessions/{session['id']}"
                client.post(path + "/commands", json={"action": "start"}).raise_for_status()
                client.post(
                    path + "/commands", json={"action": "position", "position_seconds": 3}
                ).raise_for_status()
                worker = stack.spawn(["-m", "apps.worker.main"])
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    coverage = client.get(path + "/windows").raise_for_status().json()
                    if any(row["reason"] == "superseded" for row in coverage):
                        break
                    time.sleep(0.05)
                assert any(
                    row["reason"] == "superseded" and row["sequence_end"] > row["sequence"]
                    for row in coverage
                ), coverage
                assert sum(row["state"] in {"pending", "preparing"} for row in coverage) <= 1
                client.post(path + "/commands", json={"action": "pause"}).raise_for_status()
            if name == "recovery":
                stop_processes([worker], timeout_seconds=3)
                session = (
                    client.post("/monitoring-sessions", json={**settings, "duration_seconds": 3})
                    .raise_for_status()
                    .json()
                )
                path = f"/monitoring-sessions/{session['id']}"
                client.post(path + "/commands", json={"action": "start"}).raise_for_status()
                stop_processes([api], timeout_seconds=3)
                stack.server("apps.api.main:app", api_socket)
                recovered = client.get(path).raise_for_status().json()
                assert (
                    recovered["state"] == "paused"
                    and recovered["recovery_reason"] == "process_restart"
                ), recovered
                client.post(path + "/commands", json={"action": "resume"}).raise_for_status()
                assert client.get(path).json()["state"] == "running"
                client.post(path + "/commands", json={"action": "pause"}).raise_for_status()
                coverage = client.get(path + "/windows").raise_for_status().json()
            return {
                "scenario": name,
                "status": "passed",
                "simulated": True,
                "fixture_version": prediction["fixture_version"],
                "timestamps": prediction["sampled_timestamps"],
                "export_verified": True,
                "coverage_rows": len(coverage),
            }
    finally:
        stack.close()


def main() -> None:
    """Run all CPU-only scenarios and remove every generated artifact."""

    def interrupted(_number: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        with TemporaryDirectory(prefix="fall-cpu-demo-") as temporary:
            for name in ("nominal", "overloaded", "disconnected", "recovery"):
                directory = Path(temporary) / name
                directory.mkdir()
                print(json.dumps(scenario(name, directory)), flush=True)
    finally:
        signal.signal(signal.SIGTERM, previous)
    print("PASS: four simulated scenarios; owned children reaped; temporary media/state removed.")


if __name__ == "__main__":
    main()
