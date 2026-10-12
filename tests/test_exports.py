import csv
import hashlib
import io
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from apps.api.main import create_app
from fall_detection import scheduling
from fall_detection.config import Settings
from fall_detection.exports import (
    LEGACY_REASONS,
    SAFE_REASONS,
    ExperimentExports,
    csv_export,
    json_export,
    safe_reason,
)
from fall_detection.models import PreparedFrame, PreparedInput
from fall_detection.monitoring import Monitoring, SessionCreate
from fall_detection.prediction import ACTIVITY_LABELS, InferenceClient, PipelineResult
from fall_detection.repository import Repository
from fall_detection.scheduling import LifecycleReason


@pytest.fixture
def store(tmp_path):
    repository = Repository(tmp_path / "app.sqlite3")
    repository.initialize()
    video = repository.create_sample_video()
    return repository, video, ExperimentExports(repository, tmp_path)


def complete(repository, label):
    claimed = repository.claim_next_job()
    assert claimed is not None
    result = PipelineResult(label, "data:video/base64,SECRET", [0.03, 0.19], 12.5, 25.7, 1700, 6)
    assert repository.complete_job(
        claimed.id,
        result,
        backend_kind="mock",
        model="test-model",
        fixture_version="sample-v1",
        claim_token=claimed.claim_token,
    )
    return claimed


@pytest.mark.parametrize("label", ACTIVITY_LABELS)
def test_run_export_preserves_every_label_and_temporal_identity(store, label):
    repository, video, exports = store
    job = repository.create_job(video.id, 0, 2, model="test-model", prompt_text="  Exact prompt\n")
    complete(repository, label)
    snapshot = exports.snapshot("run", job.id)
    run = snapshot["runs"][0]
    assert run["configuration"]["prompt_text"] == "  Exact prompt\n"
    assert run["configuration_id"] == job.configuration_id
    assert run["prediction"]["label"] == label
    assert run["prediction"]["sampled_timestamps"] == [0.03, 0.19]
    assert run["prediction"]["request_duration_ms"] == 12.5
    assert (run["prediction"]["prompt_tokens"], run["prediction"]["completion_tokens"]) == (1700, 6)
    assert run["provenance"] == "simulated"
    assert run["input_status"] == "synthetic"
    assert run["attempt_history"] == "complete"
    assert [event["event"] for event in run["attempt_events"]] == ["started", "succeeded"]
    serialized = json_export(snapshot)
    assert serialized == json_export(exports.snapshot("run", job.id))
    assert "SECRET" not in serialized
    assert "claim_token" not in serialized
    rows = list(csv.DictReader(io.StringIO(csv_export(snapshot))))
    assert rows[0]["label"] == label
    assert json.loads(rows[0]["sampled_timestamps"]) == [0.03, 0.19]
    assert json.loads(rows[0]["configuration"])["prompt_text"] == "  Exact prompt\n"
    assert (rows[0]["prompt_tokens"], rows[0]["completion_tokens"]) == ("1700", "6")


def test_retry_history_failure_running_and_legacy_are_explicit(store):
    repository, video, exports = store
    job = repository.create_job(video.id, 0, 2)
    first = repository.claim_next_job()
    assert repository.fail_job(
        job.id,
        "secret endpoint https://user:password@host /Users/private/movie.mp4",
        first.claim_token,
    )
    failed = exports.snapshot("run", job.id)["runs"][0]
    assert failed["prediction"] is None
    assert failed["failure"]["present"]
    assert (
        list(csv.DictReader(io.StringIO(csv_export(exports.snapshot("run", job.id)))))[0]["label"]
        == ""
    )
    repository.retry_job(job.id)
    second = repository.claim_next_job()
    running = exports.snapshot("run", job.id)["runs"][0]
    assert running["state"] == "running" and running["attempt_count"] == 2
    assert running["prediction"] is None
    assert [e["event"] for e in running["attempt_events"]] == [
        "started",
        "failed",
        "retry_requested",
        "started",
    ]
    assert repository.fail_job(job.id, "second failure", second.claim_token)
    with repository._connect() as connection:
        connection.execute("DELETE FROM attempt_events WHERE job_id=?", (job.id,))
        connection.execute(
            "UPDATE configurations SET backend_kind='unknown',preprocessing_json=? WHERE id=?",
            ('{"frames":16,"resize":448,"crop":"center"}', job.configuration_id),
        )
    legacy = exports.snapshot("run", job.id)["runs"][0]
    assert legacy["provenance"] == "unknown"
    assert legacy["attempt_history"] == "partial_legacy_unknown"
    assert "fps" not in legacy["configuration"]["preprocessing"]
    assert "password" not in json_export(exports.snapshot("run", job.id))


def test_complete_session_export_beyond_recent_history_and_all_generations(store):
    repository, video, exports = store
    monitoring = Monitoring(repository)
    request = SessionCreate(video_id=video.id, duration_seconds=6)
    configuration = monitoring.configuration(request, "test-model", "mock", "sample-v1")
    session = monitoring.create(request, configuration)
    ids = []
    for sequence in range(65):
        job = repository.create_job(video.id, 0, 2)
        complete(repository, ACTIVITY_LABELS[sequence % 16])
        ids.append(job.id)
        with repository._connect() as connection:
            connection.execute(
                """INSERT INTO monitoring_windows(id,session_id,generation,sequence,sequence_end,segment_id,start_seconds,end_seconds,available_seconds,state,job_id,created_at) VALUES (?,?,?,?,?,?,0,2,2,'submitted',?,?)""",
                (
                    f"w{sequence}",
                    session["id"],
                    sequence // 40,
                    sequence,
                    sequence,
                    session["segment_id"],
                    job.id,
                    job.created_at,
                ),
            )
    with repository._connect() as connection:
        connection.execute(
            """INSERT INTO monitoring_windows(id,session_id,generation,sequence,sequence_end,segment_id,start_seconds,end_seconds,available_seconds,state,reason,created_at) VALUES ('skip',?,2,65,90,?,2,6,6,'skipped','expired','time')""",
            (session["id"], session["segment_id"]),
        )
    assert len(repository.list_jobs(50)) == 50
    snapshot = exports.snapshot("session", session["id"])
    assert [run["id"] for run in snapshot["runs"]] == ids
    assert len(snapshot["coverage"]) == 66
    assert snapshot["coverage"][-1]["sequence_end"] == 90
    rows = list(csv.DictReader(io.StringIO(csv_export(snapshot))))
    assert len(rows) == 131
    skipped = next(row for row in rows if row["window_id"] == "skip")
    assert skipped["label"] == "" and skipped["reason"] == "expired"


def test_actual_prepared_frame_hashes_and_missing_inputs(store, tmp_path):
    repository, _, exports = store
    video = repository.create_uploaded_video("/Users/private/name.mp4", "/private/sensitive/media")
    prepared = PreparedInput(
        id="a" * 64,
        video_id=video.id,
        source_sha256="b" * 64,
        start_seconds=0,
        end_seconds=2,
        frame_count=2,
        fps=1,
        size=224,
        preprocessing_version="test",
        bundle_sha256="c" * 64,
        frames=[
            PreparedFrame(
                index=0,
                requested_seconds=0,
                actual_seconds=0.03,
                source_pts=3,
                sha256="d" * 64,
                jpeg_sha256="e" * 64,
            )
        ],
    )
    directory = tmp_path / "prepared" / prepared.id
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(prepared.model_dump_json())
    job = repository.create_job(video.id, 0, 2, prepared_input_id=prepared.id)
    snapshot = exports.snapshot("run", job.id)
    assert snapshot["runs"][0]["prepared_input"] == prepared.model_dump()
    assert "private" not in json_export(snapshot)
    (directory / "manifest.json").unlink()
    assert exports.snapshot("run", job.id)["runs"][0]["input_status"] == "unavailable"


def test_export_transaction_remains_coherent_during_completion(store, monkeypatch):
    repository, video, exports = store
    job = repository.create_job(video.id, 0, 2)
    original = exports._run

    def mutate_then_read(connection, row):
        complete(repository, "fall")
        return original(connection, row)

    monkeypatch.setattr(exports, "_run", mutate_then_read)
    snapshot = exports.snapshot("run", job.id)
    assert snapshot["runs"][0]["state"] == "queued"
    assert snapshot["runs"][0]["prediction"] is None
    assert snapshot["runs"][0]["attempt_events"] == []
    assert repository.get_job(job.id).state == "succeeded"


def test_download_endpoints_validation_and_secret_exclusion(store, tmp_path):
    repository, video, _ = store
    settings = replace(
        Settings.from_env(),
        data_dir=tmp_path,
        database_path=tmp_path / "app.sqlite3",
        inference_base_url="https://user:password@secret/v1",
    )
    job = repository.create_job(video.id, 0, 2)
    with TestClient(create_app(settings, repository)) as client:
        for output_format, content_type in [("json", "application/json"), ("csv", "text/csv")]:
            response = client.get(f"/analysis-jobs/{job.id}/export?format={output_format}")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(content_type)
            assert "attachment" in response.headers["content-disposition"]
            assert "password" not in response.text
        assert client.get(f"/analysis-jobs/{job.id}/export?format=xml").status_code == 422
        assert client.get("/analysis-jobs/missing/export").status_code == 404
        assert client.get("/monitoring-sessions/missing/export").status_code == 404


@pytest.mark.parametrize("state", ["queued", "running", "failed", "cancelled", "skipped"])
def test_nonprediction_states_remain_blank_in_csv(store, state):
    repository, video, exports = store
    job = repository.create_job(video.id, 0, 2)
    with repository._connect() as connection:
        connection.execute("UPDATE jobs SET state=? WHERE id=?", (state, job.id))
    snapshot = exports.snapshot("run", job.id)
    row = next(csv.DictReader(io.StringIO(csv_export(snapshot))))
    assert row["state"] == state and row["label"] == ""
    assert snapshot["runs"][0]["prediction"] is None


def test_expired_attempt_is_audited_without_export_side_effects(store):
    repository, video, exports = store
    job = repository.create_job(video.id, 0, 2)
    repository.claim_next_job()
    with repository._connect() as connection:
        connection.execute("UPDATE jobs SET lease_expires_at='2000-01-01' WHERE id=?", (job.id,))
    assert exports.snapshot("run", job.id)["runs"][0]["state"] == "running"
    assert repository.recover_expired() == 1
    run = exports.snapshot("run", job.id)["runs"][0]
    assert [e["event"] for e in run["attempt_events"]] == ["started", "lease_expired"]
    assert run["prediction"] is None


def test_skip_configuration_and_diagnostic_redaction(store):
    repository, video, exports = store
    monitoring = Monitoring(repository)
    request = SessionCreate(video_id=video.id, duration_seconds=6)
    config = monitoring.configuration(request, "test-model", "mock", "sample-v1")
    session = monitoring.create(request, config)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE monitoring_sessions SET recovery_reason='decoder /Users/private/secret' WHERE id=?",
            (session["id"],),
        )
        for number, reason in enumerate(
            [
                "expired",
                "superseded",
                "incomplete_tail",
                "seek",
                "process_restart",
                "decoder /Users/private/secret",
            ]
        ):
            connection.execute(
                """INSERT INTO monitoring_windows(id,session_id,generation,sequence,sequence_end,segment_id,start_seconds,end_seconds,available_seconds,state,reason,created_at) VALUES (?, ?,0,?,?,?,0,2,2,'skipped',?,'time')""",
                (str(number), session["id"], number, number, session["segment_id"], reason),
            )
    snapshot = exports.snapshot("session", session["id"])
    assert [w["reason"] for w in snapshot["coverage"]] == [
        "expired",
        "superseded",
        "incomplete_tail",
        "seek",
        "process_restart",
        "diagnostic_redacted",
    ]
    assert snapshot["coverage"][-1]["diagnostic_sha256"]
    assert "private" not in json_export(snapshot)
    rows = list(csv.DictReader(io.StringIO(csv_export(snapshot))))
    assert rows[-1]["diagnostic_sha256"] == snapshot["coverage"][-1]["diagnostic_sha256"]
    assert all(row["diagnostic_sha256"] == "" for row in rows[:-1])
    assert "private" not in csv_export(snapshot)
    assert all(row["label"] == "" for row in rows)
    assert all(json.loads(row["configuration"]) == config.model_dump() for row in rows)


def test_online_provenance_and_csv_formula_safety(store):
    repository, video, exports = store
    job = repository.create_job(video.id, 0, 2, backend_kind="vllm", model="=danger()")
    snapshot = exports.snapshot("run", job.id)
    assert snapshot["runs"][0]["provenance"] == "online_unverified"
    assert snapshot["runs"][0]["configuration"]["model"] == "=danger()"
    row = next(csv.DictReader(io.StringIO(csv_export(snapshot))))
    assert row["model"] == "'=danger()"


@pytest.mark.parametrize(
    "model", ["=danger()", "'=danger()", "''=danger()", "'plain", "\n=danger()", 'é,"\ntext']
)
def test_csv_scalar_identity_round_trips(store, model):
    repository, video, exports = store
    job = repository.create_job(video.id, 0, 2, model=model)
    row = next(csv.DictReader(io.StringIO(csv_export(exports.snapshot("run", job.id)))))
    encoded = row["model"]
    decoded = encoded[1:] if encoded.startswith("'") else encoded
    assert decoded == model
    assert not encoded.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
    assert json.loads(row["configuration"])["model"] == model


def test_lease_recovery_never_changes_a_job_without_its_event(store, monkeypatch):
    repository, video, exports = store
    job = repository.create_job(video.id, 0, 2)
    repository.claim_next_job()
    boundary = datetime(2030, 1, 1, tzinfo=UTC)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at=? WHERE id=?", (boundary.isoformat(), job.id)
        )
    times = iter(
        [boundary - timedelta(microseconds=1), boundary, boundary + timedelta(microseconds=1)]
    )
    monkeypatch.setattr(repository, "_clock", lambda: next(times, boundary + timedelta(seconds=1)))
    changed = repository.recover_expired()
    run = exports.snapshot("run", job.id)["runs"][0]
    assert changed == 0
    assert run["state"] == "running"
    assert [event["event"] for event in run["attempt_events"]] == ["started"]
    assert repository.recover_expired() == 1
    run = exports.snapshot("run", job.id)["runs"][0]
    assert [event["event"] for event in run["attempt_events"]] == ["started", "lease_expired"]
    assert run["attempt_events"][-1]["occurred_at"] == run["updated_at"]


def test_reason_columns_get_no_unclassified_sql_literals():
    # A new code written as a SQL literal instead of a LifecycleReason member would
    # be exported as diagnostic_redacted, so every literal must be classified here.
    package = Path(scheduling.__file__).parent
    pattern = re.compile(r"\b(?:recovery_)?reason\s*=\s*(?:COALESCE\([^,]*,\s*)?'([^']*)'")
    literals = {
        f"{path.name}: {value}"
        for path in package.glob("*.py")
        for value in pattern.findall(path.read_text())
        if value not in SAFE_REASONS
    }
    assert literals == {"scheduling.py: Replay starts beyond the source duration"}


def test_every_lifecycle_code_is_exportable_and_diagnostics_are_not():
    assert set(LifecycleReason) == SAFE_REASONS - LEGACY_REASONS
    assert LEGACY_REASONS.isdisjoint(LifecycleReason)
    for code in [*LifecycleReason, *LEGACY_REASONS]:
        assert safe_reason(str(code)) == code
    for diagnostic in ["Replay starts beyond the source duration", "decoder /Users/x", ""]:
        assert safe_reason(diagnostic) == "diagnostic_redacted"
    assert safe_reason(None) is None


def jpeg(shade: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (shade, shade, shade)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_live_session_export_keeps_capture_gap_and_end_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(InferenceClient, "discover_mock_identity", lambda self: "sample-v1")
    clock = [datetime(2026, 10, 2, tzinfo=UTC)]
    settings = replace(
        Settings.from_env(),
        data_dir=tmp_path,
        database_path=tmp_path / "app.sqlite3",
        backend_kind="mock",
    )
    repository = Repository(settings.database_path, clock=lambda: clock[0])
    repository.initialize()

    def send(session_id, frames, run_id):
        return client.post(
            f"/monitoring-sessions/{session_id}/frames",
            data={
                "run_id": run_id,
                "metadata": json.dumps([{"run_seq": s, "capture_seconds": t} for s, t in frames]),
            },
            files=[("frames", (f"{s}.jpg", jpeg(s * 20), "image/jpeg")) for s, _ in frames],
        )

    with TestClient(create_app(settings, repository)) as client:
        created = client.post(
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
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        url = f"/monitoring-sessions/{session_id}"
        assert client.post(f"{url}/commands", json={"action": "start"}).status_code == 200
        assert send(session_id, [(i, i * 0.25) for i in range(5)], "run-a").status_code == 200
        # A new capture run is always an explicit gap, never inferred activity.
        assert send(session_id, [(0, 0.0)], "run-b").status_code == 200
        clock[0] += timedelta(seconds=30)
        for _ in range(3):
            Monitoring(repository).tick()
        assert client.get(url).json()["recovery_reason"] == "capture_ended"
        with repository._connect() as connection:
            connection.execute(
                """INSERT INTO monitoring_windows(id,session_id,generation,sequence,
                sequence_end,segment_id,start_seconds,end_seconds,available_seconds,state,
                reason,created_at) VALUES ('diag',?,0,99,99,?,0,2,2,'failed',?,'time')""",
                (session_id, created.json()["segment_id"], "decoder /Users/private/secret"),
            )
            stored = connection.execute(
                "SELECT reason FROM monitoring_windows WHERE session_id=? AND id!='diag'",
                (session_id,),
            ).fetchall()
        # Every code the live lifecycle wrote is a shared constant, not a diagnostic.
        assert {row["reason"] for row in stored} - {None} <= set(LifecycleReason)
        exported = client.get(f"{url}/export?format=json")
        rows = list(csv.DictReader(io.StringIO(client.get(f"{url}/export?format=csv").text)))

    snapshot = json.loads(exported.text)
    assert snapshot["session"]["recovery_reason"] == "capture_ended"
    gaps = [item for item in snapshot["coverage"] if item["reason"] == "ingest_gap"]
    assert len(gaps) == 1 and gaps[0]["state"] == "skipped"
    assert gaps[0]["diagnostic_sha256"] is None
    diagnostic = next(item for item in snapshot["coverage"] if item["id"] == "diag")
    secret = hashlib.sha256(b"decoder /Users/private/secret").hexdigest()
    assert (diagnostic["reason"], diagnostic["diagnostic_sha256"]) == (
        "diagnostic_redacted",
        secret,
    )
    assert "private" not in exported.text
    coverage = {row["window_id"]: row for row in rows if row["record_type"] == "coverage"}
    assert [row["reason"] for row in coverage.values()].count("ingest_gap") == 1
    assert coverage[gaps[0]["id"]]["diagnostic_sha256"] == ""
    assert (coverage["diag"]["reason"], coverage["diag"]["diagnostic_sha256"]) == (
        "diagnostic_redacted",
        secret,
    )
    # Coverage rows never carry an activity label, so a gap is never `other`.
    assert all(row["label"] == "" for row in coverage.values())
