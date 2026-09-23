from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import apps.api.main as api
from fall_detection.repository import Repository


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = replace(
        api.settings, database_path=tmp_path / "app.sqlite3", inference_model="custom-model"
    )
    repository = Repository(settings.database_path)
    monkeypatch.setattr(api, "settings", settings)
    monkeypatch.setattr(api, "repository", repository)
    with TestClient(api.app) as client:
        yield client


def test_api_snapshots_configured_model(client):
    video = client.post("/videos/sample").json()
    response = client.post("/analysis-jobs", json={"video_id": video["id"]})
    assert response.status_code == 202
    model, _ = api.repository.get_inference_configuration(response.json()["configuration_id"])
    assert model == "custom-model"


@pytest.mark.parametrize("value", ["Infinity", "NaN", "-Infinity"])
def test_api_rejects_non_finite_timestamps(client, value):
    response = client.post("/analysis-jobs", json={"video_id": "unused", "end_seconds": value})
    assert response.status_code == 422
