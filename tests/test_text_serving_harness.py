"""Offline self-tests for smoke-test reporting; not evidence of Metal compatibility."""

import httpx
import pytest

from scripts.validate_text_serving import validate


@pytest.mark.parametrize("invalid_answer", ["banana", "other"])
def test_smoke_test_only_reports_success_when_negative_checks_fail(monkeypatch, invalid_answer):
    def fake_get(url, **_kwargs):
        body = (
            {"version": "fixture-only"}
            if url.endswith("/version")
            else {"data": [{"id": "fixture-model"}]}
        )
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    def fake_post(url, *, json, **_kwargs):
        request = httpx.Request("POST", url)
        if url.startswith("http://127.0.0.1:"):
            raise httpx.ConnectError("fixture connection refused")
        assert json["temperature"] == 0
        assert json["stream"] is False
        assert json["chat_template_kwargs"] == {"enable_thinking": False}
        if json["model"].endswith("-intentionally-unserved"):
            return httpx.Response(404, json={"error": "unserved model"}, request=request)
        truncated = json["max_tokens"] == 1
        text = json["messages"][0]["content"][0]["text"]
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
            request=request,
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
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
