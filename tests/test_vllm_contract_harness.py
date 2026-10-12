"""Self-test for the opt-in live validator; this is not GPU validation."""

import json

import httpx
import pytest

from fall_detection.prediction import THESIS_BASELINE_PROMPT, InferenceServiceError
from scripts.validate_vllm_contract import _server_identity, validate


@pytest.mark.parametrize("prompt", [THESIS_BASELINE_PROMPT, "Custom smoke-test prompt"])
def test_validator_exercises_success_and_failure_without_gpu(route_inference, prompt) -> None:
    def handler(request):
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "fixture-only"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "served-model"}]})
        assert json.loads(request.content)["messages"][0]["content"][0]["text"] == prompt
        if request.url.host == "127.0.0.1":
            raise httpx.ConnectError("fixture connection refused")
        return httpx.Response(
            200,
            json={
                "id": "fixture-completion",
                "choices": [{"message": {"content": "The best answer is: walk"}}],
            },
        )

    route_inference(handler)
    report = validate(
        "https://gpu.example/v1", "served-model", "fixture-processor", 1, prompt_text=prompt
    )
    assert report["prompt_preset"] == (
        "thesis-baseline-v1" if prompt == THESIS_BASELINE_PROMPT else "custom"
    )
    assert report["request_exact_jpegs_and_metadata"] is True
    assert report["timestamp_count"] == 16
    assert report["result_label"] == "walk"
    assert report["server_failure_state"] == "failed"
    assert report["mock_fallback"] is False


def test_identity_probe_refuses_cross_origin_redirect(route_inference) -> None:
    foreign = []

    def handler(request):
        if request.url.host != "gpu.example":
            foreign.append(request)
            return httpx.Response(200, json={"version": "fixture-only"})
        return httpx.Response(307, headers={"Location": "http://elsewhere.example/version"})

    route_inference(handler)
    with pytest.raises(InferenceServiceError, match="Refusing redirect") as refused:
        _server_identity(
            "https://gpu.example/v1",
            "served-model",
            {"Modal-Key": "dummy-key", "Modal-Secret": "dummy-secret"},
        )
    assert foreign == []
    assert "dummy" not in str(refused.value)
