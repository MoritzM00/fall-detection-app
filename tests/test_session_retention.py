"""Ended-session retention uses generated frames and disposable data directories only."""

import io
import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import apps.worker.main as worker
from apps.api.main import create_app
from apps.api.request_guards import CLIENT_HEADERS
from fall_detection.config import Settings
from fall_detection.evaluation import evaluate
from fall_detection.exports import ExperimentExports
from fall_detection.live import prepare_frames
from fall_detection.models import PreparationRequest
from fall_detection.monitoring import Monitoring, SessionCommand, SessionCreate
from fall_detection.prediction import InferenceClient, PipelineResult
from fall_detection.preparation import preparation_path
from fall_detection.repository import Repository
from fall_detection.retention import prune_media
from scripts.prune_media import evaluation_warnings, session_summary

LOCAL_API = "http://127.0.0.1"
DAY = timedelta(days=1)


def jpeg(shade: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (shade, shade, shade)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def live(tmp_path, monkeypatch):
    # Real file times are "now", so the controlled clock starts there as well.
    clock = [datetime.now(UTC)]
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
    with TestClient(
        create_app(settings, repository), base_url=LOCAL_API, headers=CLIENT_HEADERS
    ) as client:
        yield client, settings, repository, clock


def send(client, session_id, frames, run_id):
    return client.post(
        f"/monitoring-sessions/{session_id}/frames",
        data={
            "run_id": run_id,
            "metadata": json.dumps([{"run_seq": s, "capture_seconds": t} for s, t in frames]),
        },
        files=[("frames", (f"{s}.jpg", jpeg(s * 10), "image/jpeg")) for s, _ in frames],
    )


def command(client, session_id, action):
    response = client.post(f"/monitoring-sessions/{session_id}/commands", json={"action": action})
    assert response.status_code == 200, response.text


def drain(settings, repository):
    while worker.process_next_job(settings, repository):
        pass


def tree(root: Path) -> list[str]:
    """Managed media files; SQLite may add its own WAL side files on read."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if not path.name.startswith("app.sqlite3")
    )


def test_ended_live_session_frames_and_bundles_are_pruned_with_provenance_kept(live):
    client, settings, repository, clock = live
    response = client.post(
        "/monitoring-sessions/live",
        json={
            "frame_count": 3,
            "fps": 2,
            "size": 224,
            "stride_seconds": 1,
            "expiration_seconds": 2,
            "capture_fps": 4,
            "max_seconds": 60,
        },
    )
    session = response.json()
    command(client, session["id"], "start")
    assert send(client, session["id"], [(i, i * 0.25) for i in range(9)], "run-a").is_success
    drain(settings, repository)
    windows = client.get(f"/monitoring-sessions/{session['id']}/windows").json()
    bundles = {row["prepared_input_id"] for row in windows if row["prepared_input_id"]}
    assert bundles
    frames_dir = settings.data_dir / "live" / session["video_id"]
    assert len(list(frames_dir.iterdir())) == 9
    root, database = settings.data_dir, settings.database_path
    later = clock[0] + 40 * DAY

    # Running and paused sessions keep their data however old.
    assert not prune_media(root, database, now=later, apply=True).candidates
    command(client, session["id"], "pause")
    assert not prune_media(root, database, now=later, apply=True).candidates

    command(client, session["id"], "stop")
    stopped_at = clock[0]
    assert not prune_media(root, database, now=stopped_at + 10 * DAY, apply=True).candidates
    assert not prune_media(root, database, now=later, session_age=None, apply=True).candidates

    before = tree(root)
    preview = prune_media(root, database, now=later)
    assert {(c.kind, c.path) for c in preview.candidates} == {
        ("live", frames_dir),
        *{("prepared", preparation_path(root, bundle)) for bundle in bundles},
    }
    assert all(c.session_ids == (session["id"],) for c in preview.candidates)
    assert tree(root) == before
    (summary,) = session_summary(preview)
    assert f"{len(bundles)} prepared bundles" in summary and "9 live frames" in summary

    applied = prune_media(root, database, now=later, apply=True)
    assert set(applied.removed) == {c.path for c in preview.candidates}
    assert not frames_dir.exists()
    assert not any(preparation_path(root, bundle).exists() for bundle in bundles)

    # Provenance stays: frame-log rows, the source record and prepared input IDs.
    with sqlite3.connect(database) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM live_frames WHERE video_id=?", (session["video_id"],)
        ).fetchone() == (9,)
    assert repository.get_video(session["video_id"]) is not None
    exported = client.get(f"/monitoring-sessions/{session['id']}/export?format=json").json()
    assert exported["runs"]
    for run in exported["runs"]:
        assert run["prepared_input_id"] in bundles
        assert run["input_status"] == "unavailable"
        assert run["prepared_input"] is None
        assert run["configuration"]["preprocessing"]

    # Reading a pruned frame is a preparation error, never an activity.
    video = repository.get_video(session["video_id"])
    assert video is not None
    request = PreparationRequest(video_id=video.id, start_seconds=1, frame_count=3, fps=2, size=224)
    with pytest.raises(ValueError, match="no longer available"):
        prepare_frames(repository, video, request, 4, root)
    assert not prune_media(root, database, now=later, apply=True).candidates


NOW = datetime(2026, 9, 25, tzinfo=UTC)
ENDED = NOW - timedelta(days=40)


def disposable_bundle(root: Path, video_id: str) -> Path:
    target = root / "prepared" / uuid4().hex.ljust(64, "0")
    target.mkdir(parents=True)
    manifest = {
        "id": target.name,
        "video_id": video_id,
        "source_sha256": "b" * 64,
        "start_seconds": 0,
        "end_seconds": 1,
        "frame_count": 2,
        "fps": 1,
        "size": 224,
        "preprocessing_version": "test",
        "bundle_sha256": "c" * 64,
        "frames": [],
    }
    (target / "manifest.json").write_text(json.dumps(manifest))
    (target / "00.jpg").write_bytes(b"disposable frame")
    for path in [*target.iterdir(), target]:
        os.utime(path, (ENDED.timestamp(), ENDED.timestamp()))
    return target


def recorded_session(root: Path, repository: Repository, video_id: str, **window) -> str:
    """A stopped session with one window, aged by rewriting its timestamps."""
    monitor = Monitoring(repository)
    request = SessionCreate(video_id=video_id, duration_seconds=10)
    backend = window.get("backend", "mock")
    fixture = "sample-v1" if backend == "mock" else None
    session = monitor.create(request, monitor.configuration(request, "model", backend, fixture))
    monitor.command(session["id"], SessionCommand(action="stop"))
    current = monitor.get(session["id"])
    with sqlite3.connect(root / "app.sqlite3") as db:
        db.execute(
            """INSERT INTO monitoring_windows (id,session_id,generation,sequence,sequence_end,
            segment_id,start_seconds,end_seconds,available_seconds,state,reason,
            prepared_input_id,job_id,created_at) VALUES (?,?,?,0,0,?,0,1,1,?,NULL,?,?,?)""",
            (
                str(uuid4()),
                session["id"],
                current["generation"],
                current["segment_id"],
                window.get("state", "submitted"),
                window.get("prepared_input_id"),
                window.get("job_id"),
                ENDED.isoformat(),
            ),
        )
        db.execute(
            "UPDATE monitoring_sessions SET state=?, updated_at=? WHERE id=?",
            (window.get("session_state", "stopped"), ENDED.isoformat(), session["id"]),
        )
    return session["id"]


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    repository = Repository(root / "app.sqlite3")
    repository.initialize()
    media = root / "media" / f"{uuid4()}.mp4"
    media.parent.mkdir()
    media.write_bytes(b"disposable media")
    video = repository.create_uploaded_video(media.name, media.relative_to(root).as_posix())
    return root, repository, video.id


def job_for(repository: Repository, video_id: str, bundle: Path, state: str) -> str:
    job = repository.create_job(video_id, 0, 1, prepared_input_id=bundle.name)
    with sqlite3.connect(repository._database_path) as db:
        db.execute("UPDATE jobs SET state=? WHERE id=?", (state, job.id))
    return job.id


def predicted_job(repository: Repository, video_id: str, bundle: Path, backend: str) -> str:
    """A finished window job with a stored prediction from the given backend."""
    fixture = "sample-v1" if backend == "mock" else None
    job = repository.create_job(
        video_id,
        0,
        1,
        model="model",
        prepared_input_id=bundle.name,
        backend_kind=backend,
        fixture_version=fixture,
    )
    claimed = repository.claim_next_job()
    assert claimed is not None and claimed.claim_token is not None
    assert repository.complete_job(
        job.id,
        PipelineResult("walk", "walk", [0, 0.5], 1, 2),
        backend_kind=backend,
        model="model",
        fixture_version=fixture,
        claim_token=claimed.claim_token,
    )
    return job.id


def test_pruned_vllm_session_cannot_feed_a_real_report_and_preview_warns(storage):
    """Intended outcome: a pruned recorded session loses its recorded input for good."""
    root, repository, video_id = storage
    database = root / "app.sqlite3"
    mock_bundle = disposable_bundle(root, video_id)
    mock_session = recorded_session(
        root,
        repository,
        video_id,
        prepared_input_id=mock_bundle.name,
        job_id=predicted_job(repository, video_id, mock_bundle, "mock"),
    )
    assert evaluation_warnings(prune_media(root, database, now=NOW)) == []

    bundle = disposable_bundle(root, video_id)
    job = predicted_job(repository, video_id, bundle, "vllm")
    session = recorded_session(
        root, repository, video_id, prepared_input_id=bundle.name, job_id=job, backend="vllm"
    )
    exports = ExperimentExports(repository, root)
    assert exports.snapshot("session", session)["runs"][0]["input_status"] == "recorded"

    preview = prune_media(root, database, now=NOW)
    assert {c.session_ids for c in preview.candidates} == {(mock_session,), (session,)}
    assert preview.evaluated_sessions == (session,)
    (warning,) = evaluation_warnings(preview)
    assert session in warning and "--keep-sessions" in warning
    assert not prune_media(root, database, now=NOW, session_age=None).evaluated_sessions

    applied = prune_media(root, database, now=NOW, apply=True)
    assert bundle in applied.removed
    (warning,) = evaluation_warnings(applied, applied=True)
    assert session in warning and "were pruned" in warning and "--keep-sessions" not in warning
    snapshot = exports.snapshot("session", session)
    (run,) = snapshot["runs"]
    assert run["input_status"] == "unavailable" and run["prepared_input"] is None
    assert run["prediction"]["backend_kind"] == "vllm"
    truth = {
        "schema_version": 1,
        "dataset_id": "disposable",
        "split_id": "fixture",
        "purpose": "final",
        "annotation_version": "manual-v1",
        "sources": [
            {
                "source_id": video_id,
                "source_sha256": "b" * 64,
                "timebase": "source_relative_seconds",
                "observations": [{"start_seconds": 0, "end_seconds": 1}],
                "annotations": [{"start_seconds": 0, "end_seconds": 1, "label": "walk"}],
            }
        ],
    }
    with pytest.raises(ValueError, match="online provenance and recorded input"):
        evaluate(truth, [snapshot])


@pytest.mark.parametrize("job_state", ["succeeded", "failed", "cancelled", "skipped"])
def test_finished_window_input_of_an_ended_recorded_session_is_eligible(storage, job_state):
    root, repository, video_id = storage
    bundle = disposable_bundle(root, video_id)
    job = job_for(repository, video_id, bundle, job_state)
    recorded_session(root, repository, video_id, prepared_input_id=bundle.name, job_id=job)
    report = prune_media(root, root / "app.sqlite3", now=NOW, apply=True)
    assert report.removed == (bundle,)
    # The recorded source stays: its session can still be restarted.
    assert (root / repository.get_video(video_id).storage_key).exists()


@pytest.mark.parametrize(
    "case",
    ["queued_job", "running_job", "pending_window", "preparing_window", "paused", "running"],
)
def test_inputs_still_needed_or_of_open_sessions_are_protected(storage, case):
    root, repository, video_id = storage
    bundle = disposable_bundle(root, video_id)
    job_state = {"queued_job": "queued", "running_job": "running"}.get(case, "succeeded")
    window = {
        "prepared_input_id": bundle.name,
        "job_id": job_for(repository, video_id, bundle, job_state),
        "state": {"pending_window": "pending", "preparing_window": "preparing"}.get(
            case, "submitted"
        ),
        "session_state": case if case in {"paused", "running"} else "stopped",
    }
    recorded_session(root, repository, video_id, **window)
    assert not prune_media(root, root / "app.sqlite3", now=NOW, apply=True).candidates
    assert bundle.exists()


def test_bundle_shared_with_a_clip_job_or_open_session_is_protected(storage):
    root, repository, video_id = storage
    shared = disposable_bundle(root, video_id)
    recorded_session(root, repository, video_id, prepared_input_id=shared.name)
    job_for(repository, video_id, shared, "succeeded")
    second = disposable_bundle(root, video_id)
    recorded_session(root, repository, video_id, prepared_input_id=second.name)
    recorded_session(
        root, repository, video_id, prepared_input_id=second.name, session_state="paused"
    )
    report = prune_media(root, root / "app.sqlite3", now=NOW, apply=True)
    assert not report.candidates
    assert shared.exists() and second.exists()


def test_recent_session_end_and_disabled_session_pruning_keep_inputs(storage):
    root, repository, video_id = storage
    bundle = disposable_bundle(root, video_id)
    recorded_session(root, repository, video_id, prepared_input_id=bundle.name)
    database = root / "app.sqlite3"
    assert not prune_media(root, database, now=NOW, session_age=None).candidates
    assert not prune_media(root, database, now=NOW, session_age=timedelta(days=60)).candidates
    with pytest.raises(ValueError, match="session age"):
        prune_media(root, database, now=NOW, session_age=timedelta(0))
    assert prune_media(root, database, now=NOW).candidates[0].path == bundle


def test_live_frames_outside_the_managed_layout_are_not_removed(live, tmp_path):
    client, settings, repository, clock = live
    response = client.post("/monitoring-sessions/live", json={"capture_fps": 4, "fps": 2})
    session = response.json()
    command(client, session["id"], "start")
    assert send(client, session["id"], [(0, 0.0), (1, 0.25)], "run-a").is_success
    command(client, session["id"], "stop")
    frames_dir = settings.data_dir / "live" / session["video_id"]
    (frames_dir / "nested").mkdir()
    later = clock[0] + 40 * DAY
    assert not prune_media(settings.data_dir, settings.database_path, now=later).candidates
    (frames_dir / "nested").rmdir()
    outside = tmp_path.parent / f"outside-{uuid4().hex}"
    outside.mkdir()
    (frames_dir / "link.jpg").symlink_to(outside)
    assert not prune_media(settings.data_dir, settings.database_path, now=later).candidates
    (frames_dir / "link.jpg").unlink()
    outside.rmdir()
    assert prune_media(settings.data_dir, settings.database_path, now=later).candidates
