"""Self-test for the opt-in live validator; this is not GPU validation."""

import httpx
import pytest

from fall_detection.prompts import THESIS_BASELINE_PROMPT
from scripts.validate_vllm_contract import validate


@pytest.mark.parametrize("prompt", [THESIS_BASELINE_PROMPT, "Custom smoke-test prompt"])
def test_validator_exercises_success_and_failure_without_gpu(monkeypatch, prompt) -> None:
    def fake_get(url, **_kwargs):
        if url.endswith("/version"):
            return httpx.Response(
                200, json={"version": "fixture-only"}, request=httpx.Request("GET", url)
            )
        return httpx.Response(
            200, json={"data": [{"id": "served-model"}]}, request=httpx.Request("GET", url)
        )

    def fake_post(url, **_kwargs):
        assert _kwargs["json"]["messages"][0]["content"][0]["text"] == prompt
        if url.startswith("http://127.0.0.1:"):
            raise httpx.ConnectError("fixture connection refused")
        return httpx.Response(
            200,
            json={
                "id": "fixture-completion",
                "choices": [{"message": {"content": "The best answer is: walk"}}],
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    report = validate(
        "http://gpu.example/v1", "served-model", "fixture-processor", 1, prompt_text=prompt
    )
    assert report["prompt_preset"] == (
        "thesis-baseline-v1" if prompt == THESIS_BASELINE_PROMPT else "custom"
    )
    assert report["request_exact_jpegs_and_metadata"] is True
    assert report["timestamp_count"] == 16
    assert report["result_label"] == "walk"
    assert report["server_failure_state"] == "failed"
    assert report["mock_fallback"] is False
