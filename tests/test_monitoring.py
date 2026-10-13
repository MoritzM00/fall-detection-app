"""Replay contracts use controlled time and disposable source media."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import apps.worker.main as worker
from apps.api.main import create_app
from apps.api.request_guards import CLIENT_HEADERS
from fall_detection.config import Settings
from fall_detection.monitoring import Monitoring, SessionCommand, SessionCreate
from fall_detection.prediction import PipelineResult
from fall_detection.prompts import ZERO_SHOT_COT
from fall_detection.repository import Repository

LOCAL_API = "http://127.0.0.1"


@pytest.fixture
def replay(tmp_path):
    clock = [datetime(2026, 9, 27, tzinfo=UTC)]
    settings = replace(
        Settings.from_env(), data_dir=tmp_path, database_path=tmp_path / "app.sqlite3"
    )
    repository = Repository(settings.database_path, clock=lambda: clock[0])
    repository.initialize()
    video = repository.create_sample_video()
    request = SessionCreate(
        video_id=video.id,
        duration_seconds=6,
        frame_count=3,
        fps=2,
        size=224,
        stride_seconds=1,
        expiration_seconds=2,
    )
    monitor = Monitoring(repository)
    config = monitor.configuration(request, "model", "mock", "sample-v1")
    session = monitor.create(request, config)
    return settings, repository, monitor, session, request, config, clock


def command(replay, action, position=None, **kwargs):
    return replay[2].command(
        replay[3]["id"], SessionCommand(action=action, position_seconds=position, **kwargs)
    )


def history(replay):
    return replay[2].history(replay[3]["id"])


def test_no_clock_lookahead_duplicate_or_backlog(replay):
    command(replay, "start")
    replay[-1][0] += timedelta(hours=2)
    replay[2].tick()
    assert history(replay) == []
    command(replay, "position", 0.999)
    replay[2].tick()
    assert history(replay) == []
    command(replay, "position", 1)
    for _ in range(10):
        replay[2].tick()
    assert len(history(replay)) == 1
    command(replay, "position", 5)
    replay[2].tick()
    rows = history(replay)
    assert sum(row["state"] == "pending" for row in rows) == 1
    assert any(row["reason"] == "expired" for row in rows)
    assert any(
        row["reason"] == "superseded" and row["sequence_end"] > row["sequence"] for row in rows
    )
    assert all(row["end_seconds"] <= row["available_seconds"] for row in rows)
    replay[-1][0] += timedelta(seconds=3)
    replay[2].tick()
    assert all(row["state"] != "pending" for row in history(replay))


def test_tail_and_position_bounds(replay):
    request = replay[4].model_copy(update={"fps": 1, "stride_seconds": 1.5})
    config = replay[2].configuration(request, "model", "mock", "sample-v1")
    session = replay[2].create(request, config)
    replay[2].command(session["id"], SessionCommand(action="start", position_seconds=6))
    replay[2].tick()
    tail = next(
        row for row in replay[2].history(session["id"]) if row["reason"] == "incomplete_tail"
    )
    assert tail["start_seconds"] == 4.5 and tail["end_seconds"] == 6
    with pytest.raises(ValueError, match="bound"):
        replay[2].command(session["id"], SessionCommand(action="position", position_seconds=7))
    with pytest.raises(ValueError, match="Backward"):
        replay[2].command(session["id"], SessionCommand(action="position", position_seconds=1))
    with pytest.raises(ValueError, match="source duration"):
        replay[2].create(replay[4].model_copy(update={"duration_seconds": 7}), replay[5])


def test_lifecycle_and_command_retries(replay):
    command(replay, "start", 1)
    replay[2].tick()
    paused = command(replay, "pause")
    assert paused["position"] == 1 and paused["generation"] == 0
    replay[2].tick()
    assert history(replay)[0]["reason"] == "pause"
    assert command(replay, "resume")["generation"] == 0
    seek = command(replay, "seek", 0.5, command_id="seek-once")
    assert seek["generation"] == 1
    assert command(replay, "seek", 0.5, command_id="seek-once")["generation"] == 1
    with pytest.raises(ValueError, match="identity"):
        command(replay, "seek", 0.6, command_id="seek-once")
    stopped = command(replay, "stop", command_id="stop")
    assert stopped["generation"] == 2 and stopped["state"] == "stopped"
    with pytest.raises(ValueError, match="restart"):
        command(replay, "resume")
    assert command(replay, "restart", command_id="restart")["generation"] == 3
    assert command(replay, "restart", command_id="restart")["generation"] == 3


def test_config_segments_and_single_running_session(replay):
    import sqlite3

    command(replay, "start", 2)
    replay[2].tick()
    new = replay[4].model_copy(update={"prompt_text": "custom activity prompt"})
    config = replay[2].configuration(new, "model", "mock", "sample-v1")
    result = replay[2].command(
        replay[3]["id"], SessionCommand(action="configure", configuration=new), config
    )
    assert result["generation"] == 0
    assert result["segment_id"] != replay[3]["segment_id"]
    assert history(replay)[0]["reason"] == "configure"
    second = replay[2].create(replay[4], replay[5])
    with pytest.raises(sqlite3.IntegrityError):
        replay[2].command(second["id"], SessionCommand(action="start"))


def test_second_running_session_conflict_is_readable(replay):
    second = replay[2].create(replay[4], replay[5])
    with TestClient(
        create_app(replay[0], replay[1]), base_url=LOCAL_API, headers=CLIENT_HEADERS
    ) as client:
        command(replay, "start")
        response = client.post(
            f"/monitoring-sessions/{second['id']}/commands", json={"action": "start"}
        )
    assert response.status_code == 409
    assert response.json()["detail"].startswith("Another monitoring session is running")


def test_recovery_only_on_api_startup(replay):
    command(replay, "start", 1)
    replay[2].tick()
    another = Repository(replay[0].database_path)
    another.initialize()
    assert replay[2].get(replay[3]["id"])["state"] == "running"
    with TestClient(
        create_app(replay[0], replay[1]), base_url=LOCAL_API, headers=CLIENT_HEADERS
    ) as client:
        result = client.get("/monitoring-sessions/" + replay[3]["id"]).json()
        assert result["state"] == "paused" and result["recovery_reason"] == "process_restart"
        assert result["position"] == 1
        assert (
            client.get("/monitoring-sessions/" + replay[3]["id"] + "/windows").json()[0]["reason"]
            == "process_restart"
        )


def test_runtime_and_clip_fairness(replay, monkeypatch):
    monkeypatch.setattr(worker.InferenceClient, "discover_mock_identity", lambda self: "sample-v1")
    monkeypatch.setattr(
        worker,
        "run_pipeline",
        lambda **kw: PipelineResult("fall", "fall", [kw["start_seconds"], kw["end_seconds"]], 1, 2),
    )
    command(replay, "start", 1)
    clip = replay[1].create_job(replay[4].video_id, 0, 2)
    assert worker.process_next_job(replay[0], replay[1])
    assert replay[1].get_job(clip.id).state == "succeeded"
    assert history(replay)[0]["job_id"] is None
    assert worker.process_next_job(replay[0], replay[1])
    result = replay[2].get(replay[3]["id"])
    assert result["latest_job_id"] == history(replay)[0]["job_id"]
    assert replay[1].get_job(result["latest_job_id"]).prediction.label == "fall"
    assert replay[1].get_job(result["latest_job_id"]).configuration.fixture_version == "sample-v1"


def test_chain_of_thought_sessions_snapshot_the_preset_and_reach_the_parser(replay, monkeypatch):
    calls = []

    def pipeline(**kw):
        calls.append(kw)
        return PipelineResult("fall", "fall", [kw["start_seconds"], kw["end_seconds"]], 1, 2)

    monkeypatch.setattr(worker.InferenceClient, "discover_mock_identity", lambda self: "sample-v1")
    monkeypatch.setattr(worker, "run_pipeline", pipeline)
    request = replay[4].model_copy(update={"chain_of_thought": True})
    config = replay[2].configuration(request, "model", "mock", "sample-v1")
    assert config.prompt_preset == ZERO_SHOT_COT.id
    assert config.prompt_text == ZERO_SHOT_COT.prompt
    assert config.generation == ZERO_SHOT_COT.generation
    session = replay[2].create(request, config)
    replay[2].command(session["id"], SessionCommand(action="start", position_seconds=1))
    assert worker.process_next_job(replay[0], replay[1])
    job = replay[1].get_job(replay[2].get(session["id"])["latest_job_id"])
    assert job.configuration.chain_of_thought is True
    # Sessions created now send the video first; older segments keep their saved order.
    assert job.configuration.video_first is True
    assert calls[0]["chain_of_thought"] is True


def test_late_old_generation_result_kept_but_not_latest(replay):
    command(replay, "start", 1)
    assert replay[2].prepare_next(replay[0])
    first = replay[1].claim_next_job()
    command(replay, "seek", 0)
    assert replay[1].complete_job(
        first.id,
        PipelineResult("fall", "fall", [0, 1], 1, 2),
        backend_kind="mock",
        model="model",
        fixture_version="sample-v1",
        claim_token=first.claim_token,
    )
    assert replay[1].get_job(first.id).prediction.label == "fall"
    assert replay[2].get(replay[3]["id"])["latest_job_id"] is None
    assert history(replay)[0]["job_id"] == first.id


def test_global_inflight_bound_across_generation(replay):
    command(replay, "start", 1)
    replay[2].prepare_next(replay[0])
    job = replay[1].claim_next_job()
    command(replay, "seek", 0)
    command(replay, "position", 3)
    assert not replay[2].prepare_next(replay[0])
    assert sum(row["state"] == "pending" for row in history(replay)) == 1
    replay[1].fail_job(job.id, "serving failure", job.claim_token)
    assert replay[2].prepare_next(replay[0])
    with pytest.raises(ValueError, match="immutable"):
        replay[1].retry_job(job.id)


def test_queued_replay_expires_while_clips_take_priority(replay):
    command(replay, "start", 1)
    assert replay[2].prepare_next(replay[0])
    job_id = history(replay)[0]["job_id"]
    replay[-1][0] += timedelta(seconds=3)
    replay[2].tick()
    assert replay[1].get_job(job_id).state == "skipped"
    assert history(replay)[0]["reason"] == "expired"
    assert replay[1].get_job(job_id).prediction is None


def test_migration_preserves_clip_snapshot_and_prediction(replay):
    repo = replay[1]
    clip = repo.create_job(replay[4].video_id, 0, 2, prompt_text="historical prompt")
    claimed = repo.claim_next_job()
    assert repo.complete_job(
        clip.id,
        PipelineResult("fall", "fall", [0, 2], 1, 2),
        backend_kind="mock",
        model="model",
        fixture_version="sample-v1",
        claim_token=claimed.claim_token,
    )
    before = repo.get_job(clip.id)
    with repo._connect() as db:
        for table in (
            "monitoring_commands",
            "monitoring_windows",
            "monitoring_segments",
            "monitoring_sessions",
        ):
            db.execute("DROP TABLE " + table)
        db.execute("PRAGMA user_version=2")
    repo.initialize()
    assert repo.get_job(clip.id) == before
    with repo._connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 8


def test_newest_sequence_remains_latest_after_out_of_order_completion(replay):
    # Deliberately complete historical persisted identities out of order to test
    # the read contract independently of today's serial admission bound.
    command(replay, "start", 1)
    replay[2].prepare_next(replay[0])
    first = history(replay)[0]["job_id"]
    old = replay[1].claim_next_job()
    replay[1].fail_job(old.id, "interrupted", old.claim_token)
    command(replay, "position", 2)
    replay[2].prepare_next(replay[0])
    new = replay[1].claim_next_job()
    assert replay[1].complete_job(
        new.id,
        PipelineResult("walk", "walk", [1, 2], 1, 2),
        backend_kind="mock",
        model="model",
        fixture_version="sample-v1",
        claim_token=new.claim_token,
    )
    with replay[1]._connect() as db:
        db.execute(
            "UPDATE jobs SET state='running',claim_token='old',lease_expires_at=? WHERE id=?",
            ((replay[-1][0] + timedelta(seconds=90)).isoformat(), first),
        )
    assert replay[1].complete_job(
        first,
        PipelineResult("fall", "fall", [0, 1], 1, 2),
        backend_kind="mock",
        model="model",
        fixture_version="sample-v1",
        claim_token="old",
    )
    assert replay[2].get(replay[3]["id"])["latest_job_id"] == new.id


def test_pause_cannot_reopen_stopped_session(replay):
    command(replay, "stop")
    with pytest.raises(ValueError, match="restart"):
        command(replay, "pause")
    assert replay[2].get(replay[3]["id"])["state"] == "stopped"


def test_paused_abandoned_preparation_recovers_before_resume(replay):
    command(replay, "start", 1)
    replay[2].tick()
    with replay[1]._connect() as connection:
        connection.execute("UPDATE monitoring_windows SET state='preparing'")
    command(replay, "pause")
    replay[-1][0] += timedelta(seconds=91)
    replay[2].tick()
    assert history(replay)[0]["reason"] == "preparation_interrupted"
    assert history(replay)[0]["state"] == "failed"
    assert command(replay, "resume")["generation"] == 0
    command(replay, "position", 2)
    assert replay[2].prepare_next(replay[0])
    assert any(row["job_id"] for row in history(replay))


def test_invalid_monitoring_input_precedes_serving_discovery(replay, monkeypatch):
    def unavailable(self):
        raise AssertionError("invalid requests must not contact serving")

    monkeypatch.setattr(worker.InferenceClient, "discover_mock_identity", unavailable)
    with TestClient(
        create_app(replay[0], replay[1]), base_url=LOCAL_API, headers=CLIENT_HEADERS
    ) as client:
        payload = replay[4].model_dump(mode="json")
        assert (
            client.post("/monitoring-sessions", json={**payload, "video_id": "missing"}).status_code
            == 404
        )
        assert (
            client.post("/monitoring-sessions", json={**payload, "duration_seconds": 7}).status_code
            == 422
        )
        assert (
            client.post(
                "/monitoring-sessions", json={**payload, "model": "unadvertised"}
            ).status_code
            == 422
        )
        url = "/monitoring-sessions/" + replay[3]["id"] + "/commands"
        assert client.post(url, json={"action": "configure"}).status_code == 409
        assert (
            client.post(
                url,
                json={"action": "configure", "configuration": {**payload, "video_id": "missing"}},
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/monitoring-sessions/missing/commands",
                json={"action": "configure", "configuration": payload},
            ).status_code
            == 404
        )


def test_end_of_playback_admits_final_window_before_pausing(replay, monkeypatch):
    monkeypatch.setattr(worker.InferenceClient, "discover_mock_identity", lambda self: "sample-v1")
    monkeypatch.setattr(
        worker,
        "run_pipeline",
        lambda **kw: PipelineResult("lying", "lying", [kw["start_seconds"]], 1, 2),
    )
    request = replay[4].model_copy(update={"fps": 1, "stride_seconds": 1.5})
    session = replay[2].create(
        request, replay[2].configuration(request, "model", "mock", "sample-v1")
    )
    replay[2].command(session["id"], SessionCommand(action="start"))
    replay[2].command(session["id"], SessionCommand(action="position", position_seconds=6))
    replay[2].tick()
    rows = {row["sequence"]: row for row in replay[2].history(session["id"])}
    assert (rows[2]["state"], rows[2]["start_seconds"], rows[2]["end_seconds"]) == ("pending", 3, 5)
    assert (rows[3]["reason"], rows[3]["start_seconds"], rows[3]["end_seconds"]) == (
        "incomplete_tail",
        4.5,
        6,
    )
    # The final complete window is still owed a prediction, so playback end must not drop it.
    assert replay[2].get(session["id"])["state"] == "running"
    assert replay[2].prepare_next(replay[0])
    final_job = next(r for r in replay[2].history(session["id"]) if r["sequence"] == 2)["job_id"]
    assert replay[1].get_job(final_job).state == "queued"
    replay[2].tick()
    ended = replay[2].get(session["id"])
    assert ended["state"] == "paused" and ended["recovery_reason"] is None
    # Auto-pause at EOF leaves the already queued final job to complete normally.
    assert worker.process_next_job(replay[0], replay[1])
    assert replay[1].get_job(final_job).state == "succeeded"
    assert replay[1].get_job(final_job).prediction.label == "lying"
    assert replay[2].get(session["id"])["latest_job_id"] == final_job
    assert not worker.process_next_job(replay[0], replay[1])


def test_session_list_reads_every_session_with_one_connection(replay, monkeypatch):
    import sqlite3

    import fall_detection.repository as repository_module

    command(replay, "start", 1)
    replay[2].prepare_next(replay[0])
    claimed = replay[1].claim_next_job()
    assert replay[1].complete_job(
        claimed.id,
        PipelineResult("walk", "walk", [0, 1], 1, 2),
        backend_kind="mock",
        model="model",
        fixture_version="sample-v1",
        claim_token=claimed.claim_token,
    )
    replay[-1][0] += timedelta(seconds=1)
    second = replay[2].create(replay[4], replay[5])
    connect = sqlite3.connect
    opened = []
    monkeypatch.setattr(
        repository_module.sqlite3,
        "connect",
        lambda *args, **kwargs: opened.append(1) or connect(*args, **kwargs),
    )
    with TestClient(
        create_app(replay[0], replay[1]), base_url=LOCAL_API, headers=CLIENT_HEADERS
    ) as client:
        expected = [replay[2].get(second["id"]), replay[2].get(replay[3]["id"])]
        opened.clear()
        listed = client.get("/monitoring-sessions").json()
        assert len(opened) == 1
    assert expected[1]["latest_job_id"] == claimed.id
    assert listed == expected


def write_matroska(path, codec, frames=16, rate=8, options=None):
    """Write a small Matroska/WebM clip; ``live`` muxing omits duration and cues."""
    import av
    import numpy as np

    with av.open(str(path), mode="w", options=options or {}) as container:
        stream = container.add_stream(codec, rate=rate)
        stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv420p"
        for index in range(frames):
            rgb = np.full((32, 64, 3), index * 10, dtype=np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def start_uploaded_replay(replay, filename, headers_only=False, **write):
    settings, repository, monitor, _, request, config, _ = replay
    path = settings.data_dir / "media" / filename
    path.parent.mkdir(exist_ok=True)
    write_matroska(path, **write)
    if headers_only:
        # Keep only the first Cluster ID, as when a recording stops before any frame.
        cluster = bytes.fromhex("1f43b675")
        data = path.read_bytes()
        path.write_bytes(data[: data.index(cluster) + len(cluster)])
    video = repository.create_uploaded_video(filename, f"media/{filename}")
    upload = request.model_copy(update={"video_id": video.id, "duration_seconds": 100})
    session = monitor.create(upload, config)
    monitor.command(session["id"], SessionCommand(action="start"))
    monitor.verify_source(settings)
    return monitor.get(session["id"])


@pytest.mark.parametrize(
    ("filename", "codec", "options"),
    [
        ("vp9.webm", "libvpx-vp9", None),
        ("h264.mkv", "libx264", None),
        # Streamed WebM (browser MediaRecorder) carries no duration metadata at all.
        ("recorder.webm", "libvpx-vp9", {"live": "1"}),
    ],
)
def test_matroska_replay_is_verified_and_schedules_windows(replay, filename, codec, options):
    current = start_uploaded_replay(replay, filename, codec=codec, options=options)
    assert (current["state"], current["recovery_reason"]) == ("running", None)
    assert current["duration_verified"] == 1
    assert current["duration"] == pytest.approx(2)
    replay[2].command(current["id"], SessionCommand(action="position", position_seconds=2))
    replay[2].tick()
    pending = [row for row in replay[2].history(current["id"]) if row["state"] == "pending"]
    assert [row["end_seconds"] for row in pending] == [2]
    assert replay[2].prepare_next(replay[0])
    window = next(r for r in replay[2].history(current["id"]) if r["id"] == pending[0]["id"])
    assert replay[1].get_job(window["job_id"]).state == "queued"


def test_replay_source_without_duration_pauses_with_readable_reason(replay):
    current = start_uploaded_replay(
        replay, "empty.webm", headers_only=True, codec="libvpx-vp9", options={"live": "1"}
    )
    assert current["state"] == "paused" and current["duration_verified"] == 0
    assert current["recovery_reason"].startswith("Replay source has no readable duration")
    assert replay[2].history(current["id"]) == []
