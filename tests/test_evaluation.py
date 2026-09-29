import copy
import json
import subprocess
import sys

import pytest

from fall_detection.evaluation import GroundTruth, evaluate
from fall_detection.exports import ExperimentExports
from fall_detection.pipeline import PipelineResult
from fall_detection.repository import Repository
from fall_detection.taxonomy import ACTIVITY_LABELS


@pytest.fixture
def truth():
    return {
        "schema_version": 1,
        "dataset_id": "hand-fixtures-v1",
        "split_id": "fixture",
        "purpose": "synthetic",
        "annotation_version": "manual-v1",
        "sources": [
            {
                "source_id": "source-1",
                "source_sha256": "a" * 64,
                "timebase": "source_relative_seconds",
                "observations": [{"start_seconds": 0, "end_seconds": 20}],
                "annotations": [
                    {"start_seconds": start, "end_seconds": end, "label": label}
                    for start, end, label in [
                        (0, 2, "walk"),
                        (2, 4, "fall"),
                        (4, 6, "fallen"),
                        (6, 8, "walk"),
                        (8, 10, "fall"),
                        (10, 12, "walk"),
                        (12, 14, "fallen"),
                        (14, 20, "walk"),
                    ]
                ],
            }
        ],
    }


def run(identity, start, end, label, timestamps=None):
    config = {
        "id": "config-1",
        "schema_version": 1,
        "backend_kind": "mock",
        "model": "fixture-model",
        "fixture_version": "fixture-v1",
        "prompt_preset": "baseline",
        "prompt_text": "Exact prompt",
        "preprocessing": {"frames": 2, "fps": 1, "resize": 448, "crop": "center"},
        "generation": {"temperature": 0, "max_tokens": 100},
    }
    timestamps = timestamps or [start, end - 0.5]
    frames = [
        {
            "index": index,
            "requested_seconds": timestamp,
            "actual_seconds": timestamp,
            "source_pts": index,
            "sha256": "c" * 64,
            "jpeg_sha256": "d" * 64,
        }
        for index, timestamp in enumerate(timestamps)
    ]
    return {
        "id": identity,
        "video_id": "source-1",
        "source": {"id": "source-1", "source": "upload"},
        "start_seconds": start,
        "end_seconds": end,
        "configuration_id": "config-1",
        "configuration": config,
        "provenance": "simulated",
        "state": "succeeded" if label else "failed",
        "prepared_input_id": "b" * 64,
        "input_status": "recorded",
        "prepared_input": {
            "id": "b" * 64,
            "video_id": "source-1",
            "source_sha256": "a" * 64,
            "start_seconds": start,
            "end_seconds": end,
            "bundle_sha256": "e" * 64,
            "preprocessing_version": "fixture-v1",
            "frame_count": len(frames),
            "fps": 1,
            "size": 448,
            "frames": frames,
        },
        "prediction": {
            "label": label,
            "sampled_timestamps": timestamps,
            "backend_kind": "mock",
            "model": "fixture-model",
            "fixture_version": "fixture-v1",
            "provenance": "simulated",
        }
        if label
        else None,
    }


def export(runs):
    return {
        "schema_version": 1,
        "kind": "run",
        "activity_labels": list(ACTIVITY_LABELS),
        "runs": runs,
        "coverage": [],
        "segments": [],
    }


def test_hand_calculated_events_overlap_errors_delay_and_incomplete_observation(truth):
    snapshot = export(
        [
            run("a", 0, 2, "walk", [0, 1]),
            run("b", 2, 5, "fall", [2, 4]),
            run("c", 4, 6, "fallen", [4, 5]),
            run("d", 6, 8, "fall", [6, 7]),
            run("e", 8, 9, "walk", [8, 8.5]),
            run("f", 10, 12, None),
            run("h", 16, 18, "fall", [16, 17]),
        ]
    )
    before = copy.deepcopy(snapshot)
    report = evaluate(truth, [snapshot], synthetic=True)
    assert snapshot == before
    assert report["raw_runs"] == before["runs"]
    assert report["fall_fallen_binary_counts"] == {
        "true_positive": 2,
        "false_positive": 2,
        "false_negative": 1,
        "true_negative": 1,
    }
    assert report["confusion_counts"]["fall"]["walk"] == 1
    assert report["confusion_counts"]["walk"]["fall"] == 2
    assert report["unprocessed_run_counts"] == {"failed": 1}
    metrics = report["metrics"]
    assert metrics == {
        "observation_seconds": 20,
        "labeled_seconds": 20,
        "processed_observation_seconds": 11,
        "processed_labeled_seconds": 11,
        "unprocessed_seconds": 9,
        "unprocessed_fraction": 9 / 20,
        "event_count": 3,
        "missed_events": 2,
        "missed_event_fraction": 2 / 3,
        "uncovered_events": 1,
        "partially_covered_events": 1,
        "false_alarm_windows": 2,
        "false_alarm_windows_per_processed_labeled_hour": 7200 / 11,
        "false_alarm_windows_per_observation_hour": 360,
        "mean_detection_delay_seconds": 2,
    }
    assert [e["coverage"] for e in report["events"]] == ["complete", "partial", "uncovered"]
    assert [e["matched_run_id"] for e in report["events"]] == ["b", None, None]
    assert [a["classification"] for a in report["alarms"]] == [
        "matched",
        "redundant",
        "false_alarm",
        "false_alarm",
    ]


def test_all_16_labels_have_exact_unweighted_counts(truth):
    source = truth["sources"][0]
    source["observations"][0]["end_seconds"] = 32
    source["annotations"] = [
        {"start_seconds": i * 2, "end_seconds": i * 2 + 2, "label": label}
        for i, label in enumerate(ACTIVITY_LABELS)
    ]
    report = evaluate(
        truth,
        [export([run(str(i), i * 2, i * 2 + 2, label) for i, label in enumerate(ACTIVITY_LABELS)])],
        synthetic=True,
    )
    assert list(report["confusion_counts"]) == list(ACTIVITY_LABELS)
    for actual in ACTIVITY_LABELS:
        assert report["confusion_counts"][actual] == {
            label: int(label == actual) for label in ACTIVITY_LABELS
        }
    assert report["metrics"]["processed_labeled_seconds"] == 32


def test_unannotated_anchors_excluded_not_other_and_zero_denominators(truth):
    truth["sources"][0]["annotations"] = []
    report = evaluate(truth, [export([run("unknown", 0, 2, "fall")])], synthetic=True)
    assert report["excluded_windows"] == [
        {"run_id": "unknown", "reason": "unannotated_anchor", "anchor_seconds": 0}
    ]
    assert report["metrics"]["processed_observation_seconds"] == 2
    assert report["metrics"]["processed_labeled_seconds"] == 0
    assert report["metrics"]["false_alarm_windows_per_processed_labeled_hour"] is None
    assert report["metrics"]["missed_event_fraction"] is None
    assert sum(sum(row.values()) for row in report["confusion_counts"].values()) == 0
    truth["sources"][0]["observations"] = []
    empty = evaluate(truth, [export([])], synthetic=True)
    assert empty["metrics"]["unprocessed_fraction"] is None
    assert empty["metrics"]["false_alarm_windows_per_observation_hour"] is None
    assert empty["metrics"]["mean_detection_delay_seconds"] is None


def test_boundary_anchor_and_evidence_delay_not_midpoint(truth):
    report = evaluate(truth, [export([run("boundary", 2, 8, "fall", [2, 7])])], synthetic=True)
    assert report["confusion_counts"]["fall"]["fall"] == 1
    assert report["events"][0]["detection_delay_seconds"] == 5
    assert report["events"][1]["matched_run_id"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda t: t["sources"][0].update(timebase="wall_clock"),
        lambda t: t["sources"][0]["annotations"][0].update(end_seconds=0),
        lambda t: t["sources"][0]["annotations"][0].update(start_seconds=-1),
        lambda t: t["sources"][0]["annotations"][0].update(end_seconds=float("nan")),
        lambda t: t["sources"][0]["annotations"][0].update(end_seconds=float("inf")),
        lambda t: t["sources"][0]["annotations"][1].update(start_seconds=1),
        lambda t: t["sources"][0]["annotations"][0].update(label="error"),
        lambda t: t["sources"].append(copy.deepcopy(t["sources"][0])),
        lambda t: t["sources"][0]["observations"][0].update(end_seconds=19),
        lambda t: t["sources"][0].update(source_sha256="unknown"),
        lambda t: t.update(split_id=""),
        lambda t: t.update(folder_label="fall"),
    ],
)
def test_ground_truth_contract_rejects_invalid_annotations(truth, mutation):
    mutation(truth)
    with pytest.raises(ValueError):
        GroundTruth.model_validate(truth)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda r: r["prepared_input"].update(source_sha256="f" * 64), "source hash"),
        (lambda r: r["source"].update(id="wrong"), "source identity"),
        (lambda r: r["configuration"].update(id="wrong"), "configuration identity"),
        (lambda r: r["prepared_input"].update(video_id="wrong"), "prepared input identity"),
        (lambda r: r["prediction"].update(sampled_timestamps=[float("nan")]), "finite"),
        (lambda r: r["prediction"].update(sampled_timestamps=[1, 0]), "unordered"),
        (lambda r: r["prediction"].update(sampled_timestamps=[0, 1]), "timestamps mismatch"),
        (lambda r: r["prediction"].update(label="failure"), "prediction label"),
        (lambda r: r["prediction"].update(backend_kind="vllm"), "backend/model"),
        (lambda r: r.update(state="failed"), "prediction/state"),
        (lambda r: r["prepared_input"]["frames"][0].update(sha256="bad"), "frame hash"),
    ],
)
def test_export_identity_and_time_validation(truth, mutation, match):
    record = run("one", 0, 2, "walk")
    mutation(record)
    with pytest.raises(ValueError, match=match):
        evaluate(truth, [export([record])], synthetic=True)


def test_no_silent_mixed_configuration_or_duplicate_input(truth):
    first, second = run("one", 0, 2, "walk"), run("two", 2, 4, "fall")
    second["configuration"]["prompt_text"] = "tuned prompt"
    with pytest.raises(ValueError, match="mixed configurations"):
        evaluate(truth, [export([first, second])], synthetic=True)
    with pytest.raises(ValueError, match="duplicate run"):
        evaluate(truth, [export([first]), export([first])], synthetic=True)
    # Database IDs can differ while exact operational settings match.
    second["configuration"]["prompt_text"] = "Exact prompt"
    second["configuration_id"] = second["configuration"]["id"] = "config-2"
    assert evaluate(truth, [export([first, second])], synthetic=True)["metrics"]["event_count"] == 3


@pytest.mark.parametrize(
    "settings",
    [
        {"frames": 16},
        {"fps": 7.5},
        {"resize": 224},
        {"version": "different-decoder"},
        {"bundle_sha256": "f" * 64},
    ],
)
def test_rejects_prepared_input_configuration_contradiction(truth, settings):
    record = run("one", 0, 2, "walk")
    record["configuration"]["preprocessing"].update(settings)
    with pytest.raises(ValueError, match="sampling mismatch"):
        evaluate(truth, [export([record])], synthetic=True)


def test_real_mode_rejects_unknown_prepared_sampling_identity(truth):
    truth["purpose"] = "final"
    record = run("one", 0, 2, "walk")
    record["configuration"].update(backend_kind="vllm", fixture_version=None)
    record["provenance"] = "online_unverified"
    record["prediction"].update(
        backend_kind="vllm", fixture_version=None, provenance="online_unverified"
    )
    del record["prepared_input"]["fps"]
    with pytest.raises(ValueError, match="unknown legacy"):
        evaluate(truth, [export([record])])
    report = evaluate({**truth, "purpose": "synthetic"}, [export([record])], synthetic=True)
    assert {"run_id": "one", "fact": "prepared_fps_unknown"} in report["unknown_identity_facts"]


def test_source_duration_bounds_actual_frames_not_requested_window(truth):
    record = run("one", 0, 2, "walk", [0, 21])
    record["source"]["duration_seconds"] = 20
    with pytest.raises(ValueError, match="actual frame timestamp exceeds"):
        evaluate(truth, [export([record])], synthetic=True)
    record.update(prepared_input=None, prepared_input_id=None, input_status="unknown")
    with pytest.raises(ValueError, match="prediction timestamp exceeds"):
        evaluate(truth, [export([record])], synthetic=True)


def test_known_export_input_identity_mismatch_is_not_missing_legacy_identity(truth):
    record = run("failed", 0, 2, None)
    record.update(prepared_input=None, input_status="identity_mismatch")
    with pytest.raises(ValueError, match="identity mismatch"):
        evaluate(truth, [export([record])], synthetic=True)


def test_canonical_prepared_online_session_and_multiwindow_exports(tmp_path, truth):
    """Persist fixture identities through real stores; no online inference is claimed."""
    from fall_detection.models import PreparedInput
    from fall_detection.monitoring import Monitoring, SessionCreate

    repository = Repository(tmp_path / "prepared.sqlite3")
    repository.initialize()
    video = repository.create_uploaded_video("fixture.mp4", "fixture.mp4")
    monitor = Monitoring(repository)
    request = SessionCreate(video_id=video.id, duration_seconds=20, frame_count=2, fps=1)
    config = monitor.configuration(request, "fixture-model", "vllm", None)
    session = monitor.create(request, config)
    jobs = []
    for index, start in enumerate((0, 2)):
        manifest = run(str(index), start, start + 1, "walk")["prepared_input"]
        manifest.update(
            id=str(index + 1) * 64, video_id=video.id, bundle_sha256=str(index + 3) * 64
        )
        prepared = PreparedInput.model_validate(manifest)
        directory = tmp_path / "prepared" / prepared.id
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(prepared.model_dump_json())
        sampling = config.preprocessing.model_dump()
        sampling.update(
            version=prepared.preprocessing_version, bundle_sha256=prepared.bundle_sha256
        )
        job = repository.create_job(
            video.id,
            start,
            start + 1,
            model=config.model,
            backend_kind="vllm",
            prepared_input_id=prepared.id,
            preprocessing=sampling,
            generation=config.generation.model_dump(),
            prompt_text=config.prompt_text,
        )
        claimed = repository.claim_next_job()
        assert claimed is not None and claimed.claim_token is not None
        assert repository.complete_job(
            job.id,
            PipelineResult("walk", "unused", [start, start + 0.5], 1, 2),
            backend_kind="vllm",
            model=config.model,
            fixture_version=None,
            claim_token=claimed.claim_token,
        )
        jobs.append(job)
        with repository._connect() as connection:
            connection.execute(
                """INSERT INTO monitoring_windows
                (id,session_id,generation,sequence,sequence_end,segment_id,start_seconds,end_seconds,available_seconds,state,job_id,created_at)
                VALUES (?,?,0,?,?,?,?,?,?,'submitted',?,'time')""",
                (
                    str(index),
                    session["id"],
                    index,
                    index,
                    session["segment_id"],
                    start,
                    start + 1,
                    start + 1,
                    job.id,
                ),
            )
    truth["purpose"] = "final"
    truth["sources"][0]["source_id"] = video.id
    exports = ExperimentExports(repository, tmp_path)
    snapshot = exports.snapshot("session", session["id"])
    report = evaluate(truth, [snapshot])
    assert report["raw_exports"] == [snapshot]
    assert report["metrics"]["processed_observation_seconds"] == 2
    assert report["input_preprocessing_versions"] == ["fixture-v1"]
    assert "bundle_sha256" not in report["configuration"]["preprocessing"]
    assert (
        evaluate(truth, [exports.snapshot("run", job.id) for job in jobs])["metrics"]
        == report["metrics"]
    )
    conflicting = copy.deepcopy(snapshot)
    conflicting["runs"][1]["prepared_input"]["preprocessing_version"] = "different-decoder"
    conflicting["runs"][1]["configuration"]["preprocessing"]["version"] = "different-decoder"
    with pytest.raises(ValueError, match="mixed known input preprocessing"):
        evaluate(truth, [conflicting])


def test_real_mode_rejects_mock_unknown_synthetic_and_incomplete_identity(truth):
    record = run("one", 0, 2, "walk")
    with pytest.raises(ValueError, match="synthetic mode"):
        evaluate(truth, [export([record])])
    truth["purpose"] = "final"
    with pytest.raises(ValueError, match="online provenance"):
        evaluate(truth, [export([record])])
    for backend in ("vllm", "unknown"):
        provenance = "online_unverified" if backend == "vllm" else "unknown"
        record["configuration"]["backend_kind"] = record["prediction"]["backend_kind"] = backend
        record["provenance"] = record["prediction"]["provenance"] = provenance
        if backend == "unknown":
            with pytest.raises(ValueError, match="online provenance"):
                evaluate(truth, [export([record])])
        else:
            report = evaluate(truth, [export([record])])
            assert (
                report["report_kind"] == "online_quality_proposed_protocol_not_research_validated"
            )
            record["configuration"]["preprocessing"].pop("fps")
            with pytest.raises(ValueError, match="unknown legacy"):
                evaluate(truth, [export([record])])


def test_cli_consumes_actual_export_and_records_missing_legacy_facts(tmp_path, truth):
    repository = Repository(tmp_path / "app.sqlite3")
    repository.initialize()
    video = repository.create_sample_video()
    job = repository.create_job(video.id, 0, 2, model="fixture-model")
    claimed = repository.claim_next_job()
    assert claimed is not None and claimed.claim_token is not None
    assert repository.complete_job(
        job.id,
        PipelineResult("walk", "unused", [0, 1], 1, 2),
        backend_kind="mock",
        model="fixture-model",
        fixture_version="sample-v1",
        claim_token=claimed.claim_token,
    )
    snapshot = ExperimentExports(repository, tmp_path).snapshot("run", job.id)
    truth["sources"][0]["source_id"] = video.id
    truth["sources"][0]["observations"][0]["end_seconds"] = 6
    truth["sources"][0]["annotations"] = truth["sources"][0]["annotations"][:3]
    truth_path, export_path, output = (
        tmp_path / "truth.json",
        tmp_path / "export.json",
        tmp_path / "report.json",
    )
    truth_path.write_text(json.dumps(truth))
    export_path.write_text(json.dumps(snapshot))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fall_detection.evaluation",
            "--ground-truth",
            str(truth_path),
            "--export",
            str(export_path),
            "--output",
            str(output),
            "--synthetic",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert report["raw_runs"] == snapshot["runs"]
    assert report["report_kind"] == "synthetic_harness_not_model_quality"
    assert {fact["fact"] for fact in report["unknown_identity_facts"]} >= {
        "prepared_input_unavailable"
    }
    assert report["confusion_counts"]["walk"]["walk"] == 1
    before = output.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fall_detection.evaluation",
            "--ground-truth",
            str(truth_path),
            "--export",
            str(export_path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Evaluation rejected" in result.stderr
    assert output.read_bytes() == before


def test_earliest_evidence_wins_and_adjacent_observations_separate_events(truth):
    report = evaluate(
        truth,
        [
            export(
                [
                    run("slow", 2, 6, "fall", [2, 5]),
                    run("fast", 3, 5, "fallen", [3, 4]),
                ]
            )
        ],
        synthetic=True,
    )
    assert report["events"][0]["matched_run_id"] == "fast"
    assert report["events"][0]["detection_delay_seconds"] == 2
    source = truth["sources"][0]
    source["observations"] = [
        {"start_seconds": 0, "end_seconds": 4},
        {"start_seconds": 4, "end_seconds": 20},
    ]
    separated = evaluate(truth, [export([])], synthetic=True)
    assert separated["metrics"]["event_count"] == 4
    assert separated["metrics"]["missed_events"] == 4
    assert separated["metrics"]["uncovered_events"] == 4


def test_actual_frame_timestamp_outside_requested_window_preserved(truth):
    record = run("nearest", 0.1, 2, "walk", [0.09, 2.01])
    report = evaluate(truth, [export([record])], synthetic=True)
    assert report["raw_runs"][0]["prediction"]["sampled_timestamps"] == [0.09, 2.01]
    assert report["metrics"]["processed_observation_seconds"] == 1.9
    assert report["confusion_counts"]["walk"]["walk"] == 1


def test_actual_session_export_over_50_windows_with_skipped_and_failed_coverage(tmp_path, truth):
    from fall_detection.monitoring import Monitoring, SessionCreate

    repository = Repository(tmp_path / "session.sqlite3")
    repository.initialize()
    video = repository.create_sample_video()
    monitoring = Monitoring(repository)
    request = SessionCreate(video_id=video.id, duration_seconds=6)
    configuration = monitoring.configuration(request, "fixture-model", "mock", "sample-v1")
    session = monitoring.create(request, configuration)
    for index in range(55):
        job = repository.create_job(
            video.id,
            0,
            2,
            model=configuration.model,
            prompt_text=configuration.prompt_text,
            preprocessing=configuration.preprocessing.model_dump(),
            generation=configuration.generation.model_dump(),
        )
        claimed = repository.claim_next_job()
        assert claimed is not None and claimed.claim_token is not None
        if index == 54:
            assert repository.fail_job(job.id, "fixture failure", claimed.claim_token)
        else:
            assert repository.complete_job(
                job.id,
                PipelineResult("walk", "unused", [0, 1], 1, 2),
                backend_kind="mock",
                model="fixture-model",
                fixture_version="sample-v1",
                claim_token=claimed.claim_token,
            )
        with repository._connect() as connection:
            connection.execute(
                """INSERT INTO monitoring_windows
                (id,session_id,generation,sequence,sequence_end,segment_id,start_seconds,end_seconds,available_seconds,state,job_id,created_at)
                VALUES (?,?,0,?,?,?,0,2,2,'submitted',?,'time')""",
                (f"window-{index}", session["id"], index, index, session["segment_id"], job.id),
            )
    with repository._connect() as connection:
        connection.execute(
            """INSERT INTO monitoring_windows
            (id,session_id,generation,sequence,sequence_end,segment_id,start_seconds,end_seconds,available_seconds,state,reason,created_at)
            VALUES ('skip',?,0,55,60,?,2,6,6,'skipped','expired','time')""",
            (session["id"], session["segment_id"]),
        )
    truth["sources"][0]["source_id"] = video.id
    truth["sources"][0]["observations"][0]["end_seconds"] = 6
    truth["sources"][0]["annotations"] = truth["sources"][0]["annotations"][:3]
    snapshot = ExperimentExports(repository, tmp_path).snapshot("session", session["id"])
    report = evaluate(truth, [snapshot], synthetic=True)
    assert len(report["raw_runs"]) == 55
    assert len(report["raw_coverage"]) == 56
    assert report["confusion_counts"]["walk"]["walk"] == 54
    assert report["metrics"]["processed_observation_seconds"] == 2
    assert report["metrics"]["unprocessed_seconds"] == 4
    assert report["metrics"]["uncovered_events"] == 1
    assert report["unprocessed_run_counts"] == {"failed": 1}
    other = copy.deepcopy(snapshot)
    other["segments"][0]["configuration"]["prompt_text"] = "different"
    with pytest.raises(ValueError, match="mixed session segment"):
        evaluate(truth, [other], synthetic=True)
    other = copy.deepcopy(snapshot)
    other["coverage"][-1]["job_id"] = "missing"
    with pytest.raises(ValueError, match="absent run"):
        evaluate(truth, [other], synthetic=True)


def test_empty_session_segments_still_reject_mixed_backends(truth):
    snapshot = export([])
    snapshot.update(kind="session", source={"id": "source-1"}, session={"video_id": "source-1"})
    config = run("one", 0, 2, "walk")["configuration"]
    snapshot["segments"] = [
        {"id": "segment-1", "configuration": config, "provenance": "simulated"},
        {
            "id": "segment-2",
            "configuration": {**config, "backend_kind": "vllm"},
            "provenance": "online_unverified",
        },
    ]
    with pytest.raises(ValueError, match="mixed session segment"):
        evaluate(truth, [snapshot], synthetic=True)


def test_cli_rejects_input_overwrite_and_duplicate_json_keys(tmp_path, truth):
    truth_path, export_path = tmp_path / "truth.json", tmp_path / "export.json"
    truth_path.write_text(json.dumps(truth))
    export_path.write_text(json.dumps(export([])))
    command = [
        sys.executable,
        "-m",
        "fall_detection.evaluation",
        "--ground-truth",
        str(truth_path),
        "--export",
        str(export_path),
        "--output",
        str(export_path),
        "--synthetic",
    ]
    before = export_path.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 2 and "overwrite" in result.stderr
    assert export_path.read_bytes() == before
    command[command.index("--output") + 1] = str(tmp_path / "report.json")
    truth_path.write_text('{"schema_version":1,"schema_version":2}')
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 2 and "duplicate JSON key" in result.stderr


def test_real_quality_keeps_preparation_failures_only_with_verified_source(truth):
    truth["purpose"] = "final"
    successful, failed = run("success", 0, 2, "walk"), run("failure", 2, 4, None)
    for record in (successful, failed):
        record["configuration"]["backend_kind"] = "vllm"
        record["configuration"]["fixture_version"] = None
        record["provenance"] = "online_unverified"
        if record["prediction"]:
            record["prediction"].update(
                backend_kind="vllm", fixture_version=None, provenance="online_unverified"
            )
    failed.update(prepared_input=None, prepared_input_id=None, input_status="unknown")
    report = evaluate(truth, [export([successful, failed])])
    assert report["unprocessed_run_counts"] == {"failed": 1}
    assert report["metrics"]["missed_events"] == 3
    assert {fact["fact"] for fact in report["unknown_identity_facts"]} == {
        "prepared_input_unavailable",
        "source_hash_unverified",
    }
    with pytest.raises(ValueError, match="verified source hash"):
        evaluate(truth, [export([failed])])
