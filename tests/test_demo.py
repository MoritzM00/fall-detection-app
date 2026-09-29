"""Ownership and cleanup contracts for the disposable CPU scenario runner."""

import subprocess
import sys

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
