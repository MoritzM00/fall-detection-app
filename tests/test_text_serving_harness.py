"""Offline self-tests for smoke-test reporting; not evidence of Metal compatibility."""

import json

import httpx
import pytest

from scripts.validate_text_serving import validate


@pytest.mark.parametrize("invalid_answer", ["banana", "other"])
def test_smoke_test_only_reports_success_when_negative_checks_fail(route_inference, invalid_answer):
    def handler(request):
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "fixture-only"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "fixture-model"}]})
        if request.url.host == "127.0.0.1":
            raise httpx.ConnectError("fixture connection refused")
        body = json.loads(request.content)
        assert body["temperature"] == 0
        assert body["stream"] is False
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        if body["model"].endswith("-intentionally-unserved"):
            return httpx.Response(404, json={"error": "unserved model"})
        truncated = body["max_tokens"] == 1
        text = body["messages"][0]["content"][0]["text"]
        answer = "fragment" if truncated else text.split("nothing else: ")[1]
        if answer == "banana":
            answer = invalid_answer
        return httpx.Response(
            200,
            json={
                "id": "fixture-completion",
                "choices": [
                    {
                        "finish_reason": "length" if truncated else "stop",
                        "message": {"content": answer},
                    }
                ],
            },
        )

    route_inference(handler)
    if invalid_answer == "other":
        with pytest.raises(RuntimeError, match="expected non-activity output to fail"):
            validate("http://fixture.example/v1", "fixture-model", 1)
    else:
        report = validate("http://fixture.example/v1", "fixture-model", 1)
        assert len(report["answers"]) == 16
        assert report["invalid_label_rejected"] is True
        assert report["truncation_rejected"] is True
        assert report["unserved_model_rejected"] is True
        assert report["unavailable_endpoint_rejected"] is True
        assert report["video_contract_validated"] is False
