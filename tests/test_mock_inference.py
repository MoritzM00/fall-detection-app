import pytest
from fastapi.testclient import TestClient

import apps.mock_inference.main as mock_server
from fall_detection.pipeline import build_inference_payload
from fall_detection.prompts import THESIS_BASELINE_PROMPT


@pytest.fixture(autouse=True)
def fast_default(monkeypatch):
    manifest = mock_server.MANIFEST
    monkeypatch.setattr(
        mock_server,
        "MANIFEST",
        manifest.model_copy(
            update={"default": manifest.default.model_copy(update={"delay_ms": 0})}
        ),
    )


def test_mock_implements_used_chat_completion_contract(monkeypatch) -> None:
    client = TestClient(mock_server.app)
    payload = build_inference_payload(
        mock_server.MANIFEST.model,
        "data:video/mp4;base64,ZmFsbC1kZXRlY3Rpb24tc3ludGhldGljLWNvcnJpZG9yLXYx",
        THESIS_BASELINE_PROMPT,
    )

    response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "The best answer is: fall"


def test_mock_rejects_missing_video_payload(monkeypatch) -> None:
    client = TestClient(mock_server.app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": mock_server.MANIFEST.model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "prompt"}]}],
        },
    )

    assert response.status_code == 400


@pytest.mark.parametrize("extra", [None, "invalid", {"type": []}])
def test_mock_rejects_malformed_content_parts(extra) -> None:
    payload = build_inference_payload(
        mock_server.MANIFEST.model,
        "data:video/mp4;base64,ZmFsbC1kZXRlY3Rpb24tc3ludGhldGljLWNvcnJpZG9yLXYx",
        "prompt",
    )
    payload["messages"][0]["content"].append(extra)
    response = TestClient(mock_server.app).post("/v1/chat/completions", json=payload)
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("field", "value", "expected_status"),
    [
        ("model", "unserved-model", 404),
        ("temperature", {"invalid": True}, 422),
        ("max_tokens", 0, 422),
        ("stream", True, 400),
    ],
)
def test_mock_validates_completion_settings(field, value, expected_status) -> None:
    payload = build_inference_payload(
        mock_server.MANIFEST.model,
        "data:video/mp4;base64,ZmFsbC1kZXRlY3Rpb24tc3ludGhldGljLWNvcnJpZG9yLXYx",
        "prompt",
    )
    payload[field] = value

    response = TestClient(mock_server.app).post("/v1/chat/completions", json=payload)

    assert response.status_code == expected_status


def test_mock_rejects_video_without_url() -> None:
    payload = build_inference_payload(
        mock_server.MANIFEST.model,
        "data:video/mp4;base64,ZmFsbC1kZXRlY3Rpb24tc3ludGhldGljLWNvcnJpZG9yLXYx",
        "prompt",
    )
    payload["messages"][0]["content"][1]["video_url"] = {}

    response = TestClient(mock_server.app).post("/v1/chat/completions", json=payload)

    assert response.status_code == 400
