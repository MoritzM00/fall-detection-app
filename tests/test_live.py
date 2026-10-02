"""Live camera ingest contracts with generated JPEG frames and a controlled clock."""

import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import apps.worker.main as worker
from apps.api.main import create_app
from fall_detection.config import Settings
from fall_detection.inference import InferenceClient
from fall_detection.live import LIVE_PREPROCESSING_VERSION, IngestGapError, prepare_frames
from fall_detection.models import PreparationRequest
from fall_detection.monitoring import Monitoring
from fall_detection.pipeline import PipelineResult
from fall_detection.preparation import load_prepared_input
from fall_detection.repository import Repository


def jpeg(shade: int = 128, size: tuple[int, int] = (64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (shade, shade, shade)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def live(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 2, tzinfo=UTC)]
    settings = replace(
        Settings.from_env(),
        data_dir=tmp_path,
        database_path=tmp_path / "app.sqlite3",
        backend_kind="mock",
    )
    repository = Repository(settings.database_path, clock=lambda: clock[0])
    repository.initialize()
    monkeypatch.setattr(InferenceClient, "discover_mock_identity", lambda self: "sample-v1")
    monkeypatch.setattr(
        worker,
        "run_pipeline",
        lambda **kw: PipelineResult("walk", "walk", kw["sampled_timestamps"], 1, 2),
    )
    with TestClient(create_app(settings, repository)) as client:
        yield client, settings, repository, clock


def create(client, **overrides) -> dict:
    body = {
        "frame_count": 3,
        "fps": 2,
        "size": 224,
        "stride_seconds": 1,
        "expiration_seconds": 2,
        "capture_fps": 4,
        "max_seconds": 60,
        **overrides,
    }
    response = client.post("/monitoring-sessions/live", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def send(client, session_id, frames, blobs=None, run_id="run-a"):
    """Post one capture run's frames given as (run_seq, run-relative seconds) pairs."""
    blobs = blobs or [jpeg(seq % 200) for seq, _ in frames]
    return client.post(
        f"/monitoring-sessions/{session_id}/frames",
        data={
            "run_id": run_id,
            "metadata": json.dumps([{"run_seq": s, "capture_seconds": t} for s, t in frames]),
        },
        files=[
            ("frames", (f"{s}.jpg", blob, "image/jpeg"))
            for (s, _), blob in zip(frames, blobs, strict=True)
        ],
    )


def run(client, session_id, action):
    return client.post(f"/monitoring-sessions/{session_id}/commands", json={"action": action})


def history(client, session_id):
    return client.get(f"/monitoring-sessions/{session_id}/windows").json()


def drain(settings, repository):
    for _ in range(10):
        if not worker.process_next_job(settings, repository):
            return
    raise AssertionError("worker did not settle")


def test_live_frames_flow_into_an_ordinary_prediction(live):
    client, settings, repository, _ = live
    session = create(client)
    assert (session["source_kind"], session["state"], session["capture_fps"]) == (
        "live",
        "paused",
        4,
    )
    assert run(client, session["id"], "start").status_code == 200
    accepted = send(client, session["id"], [(i, i * 0.25) for i in range(9)])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {
        "accepted": 9,
        "run_seq_high": 8,
        "watermark_seconds": 2.0,
        "state": "running",
    }

    drain(settings, repository)
    rows = {row["sequence"]: row for row in history(client, session["id"])}
    assert rows[0]["reason"] == "superseded"
    assert (rows[1]["start_seconds"], rows[1]["end_seconds"]) == (1, 2)
    job = repository.get_job(rows[1]["job_id"])
    assert job.state == "succeeded" and job.prediction.label == "walk"
    assert job.video_id == session["video_id"]
    prepared = load_prepared_input(settings.data_dir, job.prepared_input_id)
    assert prepared.preprocessing_version == LIVE_PREPROCESSING_VERSION
    assert job.configuration.preprocessing.version == LIVE_PREPROCESSING_VERSION
    # Each sampled timestamp maps to an actually captured frame, never past the window end.
    assert [f.actual_seconds for f in prepared.frames] == [1.0, 1.5, 2.0]
    assert [f.source_pts for f in prepared.frames] == [4, 6, 8]
    assert job.prediction.sampled_timestamps == [1.0, 1.5, 2.0]
    exported = client.get(f"/monitoring-sessions/{session['id']}/export?format=json")
    assert exported.status_code == 200, exported.text
    assert client.get(f"/monitoring-sessions/{session['id']}/export?format=csv").status_code == 200


def test_ingest_is_idempotent_and_rejects_conflicts(live):
    client, _, _, _ = live
    session = create(client)
    run(client, session["id"], "start")
    batch = [(0, 0.0), (1, 0.25)]
    assert send(client, session["id"], batch).json()["accepted"] == 2
    assert send(client, session["id"], batch).json()["accepted"] == 0
    changed = send(client, session["id"], batch, [jpeg(1), jpeg(2)])
    assert changed.status_code == 409 and "other content" in changed.json()["detail"]
    skipped = send(client, session["id"], [(3, 0.75)])
    assert skipped.status_code == 409 and "resend from 2" in skipped.json()["detail"]
    assert send(client, session["id"], [(2, 0.25)]).status_code == 409
    assert send(client, session["id"], [(2, 0.5), (3, 0.5)]).status_code == 422
    assert send(client, session["id"], [(2, 0.5)], [b"not a jpeg"]).status_code == 422
    assert send(client, session["id"], [(2, 61.0)]).status_code == 409

    fresh = send(client, session["id"], [(5, 0.0)], run_id="run-b")
    assert fresh.status_code == 409 and "run_seq 0" in fresh.json()["detail"]

    run(client, session["id"], "pause")
    assert send(client, session["id"], [(2, 0.5)]).status_code == 409
    # A resend after pausing is still acknowledged, so client retries settle.
    assert send(client, session["id"], batch).status_code == 200


def test_each_capture_run_is_placed_by_the_server_clock_after_a_gap(live):
    client, settings, repository, clock = live
    session = create(client)
    run(client, session["id"], "start")
    send(client, session["id"], [(i, i * 0.25) for i in range(5)], run_id="run-a")
    Monitoring(repository).tick()
    # After a reload the session has paused; the resumed page restarts its clock at
    # zero, and the server, not the client, places the new run on the timeline.
    clock[0] += timedelta(seconds=30)
    Monitoring(repository).tick()
    assert client.get(f"/monitoring-sessions/{session['id']}").json()["state"] == "paused"
    run(client, session["id"], "resume")
    placed = send(client, session["id"], [(i, i * 0.25) for i in range(5)], run_id="run-b")
    assert placed.json()["watermark_seconds"] == 32.0
    gap = next(row for row in history(client, session["id"]) if row["reason"] == "ingest_gap")
    assert (gap["start_seconds"], gap["end_seconds"]) == (1, 31.0)
    drain(settings, repository)
    windows = [row for row in history(client, session["id"]) if row["job_id"]]
    assert all(row["end_seconds"] <= 1 or row["start_seconds"] >= 31.0 for row in windows)
    assert any(row["start_seconds"] == 31.0 for row in windows)
    # The older run is finished: retries are acknowledged, new frames are refused.
    assert send(client, session["id"], [(0, 0.0)], run_id="run-a").json()["accepted"] == 0
    stale = send(client, session["id"], [(5, 1.25)], run_id="run-a")
    assert stale.status_code == 409 and "superseded" in stale.json()["detail"]


def test_an_immediate_new_run_is_still_a_gap(live):
    client, _, _, _ = live
    session = create(client)
    run(client, session["id"], "start")
    send(client, session["id"], [(i, i * 0.25) for i in range(5)], run_id="run-a")
    send(client, session["id"], [(0, 0.0)], run_id="run-b")
    gaps = [row for row in history(client, session["id"]) if row["reason"] == "ingest_gap"]
    assert len(gaps) == 1 and gaps[0]["end_seconds"] == 1.25


def test_capture_gap_is_explicit_coverage_and_rebases_windows(live):
    client, settings, repository, _ = live
    session = create(client)
    run(client, session["id"], "start")
    send(client, session["id"], [(i, i * 0.25) for i in range(5)])
    Monitoring(repository).tick()
    send(client, session["id"], [(5 + i, 3.0 + i * 0.25) for i in range(5)])
    rows = {row["sequence"]: row for row in history(client, session["id"])}
    assert (rows[0]["start_seconds"], rows[0]["end_seconds"]) == (0, 1)
    gap = rows[1]
    assert (gap["state"], gap["reason"], gap["start_seconds"], gap["end_seconds"]) == (
        "skipped",
        "ingest_gap",
        1,
        3.0,
    )
    drain(settings, repository)
    rows = {row["sequence"]: row for row in history(client, session["id"])}
    # The grid restarts at the first frame after the gap; no window spans it.
    assert (rows[2]["start_seconds"], rows[2]["end_seconds"]) == (3.0, 4.0)
    assert repository.get_job(rows[2]["job_id"]).state == "succeeded"
    assert all(
        row["end_seconds"] <= 1 or row["start_seconds"] >= 3.0 or row["reason"] == "ingest_gap"
        for row in rows.values()
    )


def test_late_first_frame_rebases_instead_of_failing(live):
    client, settings, repository, _ = live
    session = create(client)
    run(client, session["id"], "start")
    send(client, session["id"], [(i, 0.6 + i * 0.25) for i in range(5)])
    drain(settings, repository)
    rows = history(client, session["id"])
    assert any(row["reason"] == "ingest_gap" and row["end_seconds"] == 0.6 for row in rows)
    assert any(row["job_id"] and row["start_seconds"] == 0.6 for row in rows)


def test_live_timeline_cannot_be_positioned_and_restarts_at_live_edge(live):
    client, _, _, _ = live
    session = create(client)
    run(client, session["id"], "start")
    send(client, session["id"], [(i, i * 0.25) for i in range(5)])
    url = f"/monitoring-sessions/{session['id']}/commands"
    assert client.post(url, json={"action": "position", "position_seconds": 1}).status_code == 409
    assert client.post(url, json={"action": "seek", "position_seconds": 0}).status_code == 409
    assert client.post(url, json={"action": "restart", "position_seconds": 0}).status_code == 409
    restarted = run(client, session["id"], "restart").json()
    assert restarted["generation"] == 1
    assert restarted["position"] == restarted["origin"] == 1.0


def test_capture_ended_pauses_with_a_visible_reason(live):
    client, _, repository, clock = live
    session = create(client)
    run(client, session["id"], "start")
    clock[0] += timedelta(seconds=1)
    send(client, session["id"], [(0, 0.0)])
    clock[0] += timedelta(seconds=1.5)
    Monitoring(repository).tick()
    assert client.get(f"/monitoring-sessions/{session['id']}").json()["state"] == "running"
    clock[0] += timedelta(seconds=1)
    Monitoring(repository).tick()
    ended = client.get(f"/monitoring-sessions/{session['id']}").json()
    assert (ended["state"], ended["recovery_reason"]) == ("paused", "capture_ended")
    # Resume grants a fresh ingest timeout before the camera has delivered again.
    assert run(client, session["id"], "resume").json()["state"] == "running"
    Monitoring(repository).tick()
    assert client.get(f"/monitoring-sessions/{session['id']}").json()["state"] == "running"


def test_live_sources_are_monitoring_only(live):
    client, _, _, _ = live
    session = create(client)
    video_id = session["video_id"]
    prepared = client.post(
        "/prepared-inputs",
        json={"video_id": video_id, "start_seconds": 0, "frame_count": 2, "fps": 1, "size": 224},
    )
    assert prepared.status_code == 422
    job = client.post(
        "/analysis-jobs",
        json={
            "video_id": video_id,
            "start_seconds": 0,
            "end_seconds": 1,
            "prepared_input_id": None,
        },
    )
    assert job.status_code == 422
    recording = client.post(
        "/monitoring-sessions", json={"video_id": video_id, "duration_seconds": 10}
    )
    assert recording.status_code == 422
    assert client.get(f"/videos/{video_id}/media").status_code == 404


def test_sampling_fps_is_bounded_by_capture_fps(live):
    client, _, _, _ = live
    response = client.post("/monitoring-sessions/live", json={"fps": 10, "capture_fps": 5})
    assert response.status_code == 422
    session = create(client)
    url = f"/monitoring-sessions/{session['id']}/commands"
    configure = {
        "action": "configure",
        "configuration": {"video_id": session["video_id"], "frame_count": 3, "fps": 8},
    }
    assert client.post(url, json=configure).status_code == 409


def test_frame_log_preparation_reports_gaps_as_ingest_gaps(live):
    client, settings, repository, _ = live
    session = create(client)
    run(client, session["id"], "start")
    send(client, session["id"], [(0, 0.0), (1, 0.25)])
    video = repository.get_video(session["video_id"])
    request = PreparationRequest(video_id=video.id, start_seconds=0, frame_count=3, fps=2, size=224)
    with pytest.raises(IngestGapError):
        prepare_frames(repository, video, request, 4, settings.data_dir)


def test_migration_adds_live_schema_to_existing_databases(tmp_path):
    repository = Repository(tmp_path / "app.sqlite3")
    repository.initialize()
    video = repository.create_sample_video()
    with repository._connect(foreign_keys=False) as db:
        db.execute(
            """INSERT INTO monitoring_sessions (id,video_id,state,generation,segment_id,position,
            duration,origin,next_sequence,stride,expiration,created_at,updated_at)
            VALUES ('old','sample-corridor-v1','paused',0,'s',0,6,0,0,1,2,'t','t')"""
        )
        for column in ("source_kind", "capture_fps", "last_frame_at"):
            db.execute(f"ALTER TABLE monitoring_sessions DROP COLUMN {column}")
        db.execute("DROP TABLE live_frames")
        db.execute("DROP TABLE live_runs")
        db.execute("""CREATE TABLE videos_old (id TEXT PRIMARY KEY, filename TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('upload', 'synthetic', 'dataset')),
            storage_key TEXT, duration_seconds REAL, created_at TEXT NOT NULL)""")
        db.execute("INSERT INTO videos_old SELECT * FROM videos")
        db.execute("DROP TABLE videos")
        db.execute("ALTER TABLE videos_old RENAME TO videos")
        db.execute("PRAGMA user_version=4")
        db.commit()
    repository.initialize()
    assert repository.get_video(video.id) == video
    assert repository.create_live_source().source == "live"
    with repository._connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        old = db.execute("SELECT * FROM monitoring_sessions WHERE id='old'").fetchone()
        assert (old["source_kind"], old["capture_fps"]) == ("recording", None)
