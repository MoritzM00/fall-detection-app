import pytest
from fastapi.testclient import TestClient

import apps.mock_inference.main as mock_server
from fall_detection.pipeline import build_inference_payload
from fall_detection.prompts import THESIS_BASELINE_PROMPT


def test_mock_implements_used_chat_completion_contract(monkeypatch) -> None:
    monkeypatch.setattr(mock_server, "DELAY_MS", 0)
    client = TestClient(mock_server.app)
    payload = build_inference_payload(
        mock_server.SERVED_MODEL,
        "data:video/mp4;base64,bW9jaw==",
        THESIS_BASELINE_PROMPT,
    )

    response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "The best answer is: fall"


def test_mock_rejects_missing_video_payload(monkeypatch) -> None:
    monkeypatch.setattr(mock_server, "DELAY_MS", 0)
    client = TestClient(mock_server.app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": mock_server.SERVED_MODEL,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "prompt"}]}],
        },
    )

    assert response.status_code == 400


@pytest.mark.parametrize("extra", [None, "invalid", {"type": []}])
def test_mock_rejects_malformed_content_parts(extra) -> None:
    payload = build_inference_payload(
        mock_server.SERVED_MODEL, "data:video/mp4;base64,bW9jaw==", "prompt"
    )
    payload["messages"][0]["content"].append(extra)
    response = TestClient(mock_server.app).post("/v1/chat/completions", json=payload)
    assert response.status_code == 400
