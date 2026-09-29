"""Ownership and cleanup contracts for the disposable CPU scenario runner."""

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

import pytest

from scripts.demo import Stack


def test_demo_ignores_runtime_and_fixture_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("FALL_DETECTION_BACKEND_KIND", "vllm")
    monkeypatch.setenv("FALL_DETECTION_DATA_DIR", "/unrelated/private/media")
    monkeypatch.setenv("MOCK_INFERENCE_LABEL", "other")
    stack = Stack(tmp_path)
    assert stack.environment["FALL_DETECTION_BACKEND_KIND"] == "mock"
    assert stack.environment["FALL_DETECTION_DATA_DIR"] == str(tmp_path / "data")
    assert "MOCK_INFERENCE_LABEL" not in stack.environment


def test_partial_startup_cleanup_reaps_only_owned_children(tmp_path, monkeypatch):
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    stack = Stack(tmp_path)
    try:
        owned = stack.spawn(["-c", "import time; time.sleep(30)"])

        def failed_start(*args, **kwargs):
            raise OSError("failed subsequent startup")

        monkeypatch.setattr("scripts.demo.subprocess.Popen", failed_start)
        with pytest.raises(OSError, match="subsequent startup"):
            try:
                stack.spawn(["-c", "pass"])
            finally:
                stack.close()
        assert owned.poll() is not None
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
        stack.close()


def test_cleanup_stops_descendant_after_owned_parent_exits(tmp_path):
    marker = tmp_path / "descendant.txt"
    stack = Stack(tmp_path)
    script = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', "
        + repr(
            "import signal, time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"p = Path({str(marker)!r}); "
            "\nwhile True:\n p.write_text(str(time.monotonic()))\n time.sleep(0.02)"
        )
        + "])"
    )
    try:
        parent = stack.spawn(["-c", script])
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        stack.close()
        time.sleep(0.1)
        content = marker.read_text()
        time.sleep(0.1)
        assert marker.read_text() == content
    finally:
        # Test failure must not itself strand the deliberately resistant child.
        with suppress(ProcessLookupError):
            os.killpg(parent.pid, signal.SIGKILL)
        stack.close()


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_interruption_reaps_children_and_removes_generated_state(tmp_path, signum):
    marker = tmp_path / "owned.json"
    script = f"""
import json, time
from scripts import demo

def scenario(name, directory):
    stack = demo.Stack(directory)
    try:
        child = stack.spawn(["-c", "import time; time.sleep(30)"])
        from pathlib import Path
        Path({str(marker)!r}).write_text(json.dumps([child.pid, str(directory.parent)]))
        time.sleep(30)
    finally:
        stack.close()

demo.scenario = scenario
demo.main()
"""
    runner = subprocess.Popen([sys.executable, "-c", script], stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        import json
        from pathlib import Path

        child_pid, generated_directory = json.loads(marker.read_text())
        runner.send_signal(signum)
        assert runner.wait(timeout=5) != 0
        assert not Path(generated_directory).exists()
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if runner.poll() is None:
            runner.terminate()
            runner.wait(timeout=5)
