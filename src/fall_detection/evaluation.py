"""Proposed protocol v1: offline metrics, never research accuracy evidence."""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fall_detection.taxonomy import ACTIVITY_LABELS, ActivityLabel

PROTOCOL = "first-sample-raw-window-v1"
POSITIVE = {"fall", "fallen"}
TIMEBASE = "source_relative_seconds"


class Interval(BaseModel):
    """A finite, positive half-open source-relative interval."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def positive(self) -> "Interval":
        """Reject reversed and empty intervals."""
        if self.end_seconds <= self.start_seconds:
            raise ValueError("interval must have positive duration")
        return self


class Annotation(Interval):
    """An explicit activity annotation; folders cannot supply this label."""

    label: ActivityLabel


class GroundTruthSource(BaseModel):
    """Bind annotations and declared observation to one exact decoded source."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    source_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    timebase: Literal["source_relative_seconds"]
    observations: list[Interval]
    annotations: list[Annotation]

    @model_validator(mode="after")
    def ordered(self) -> "GroundTruthSource":
        """Disallow overlap and annotations outside declared observation."""
        for intervals in (self.observations, self.annotations):
            if any(
                a.end_seconds > b.start_seconds
                for a, b in zip(intervals, intervals[1:], strict=False)
            ):
                raise ValueError("intervals must be ordered and nonoverlapping")
        for annotation in self.annotations:
            if not any(
                o.start_seconds <= annotation.start_seconds
                and annotation.end_seconds <= o.end_seconds
                for o in self.observations
            ):
                raise ValueError("annotation outside observation interval")
        return self


class GroundTruth(BaseModel):
    """Explicit dataset/split identity, independent of prediction exports."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    split_id: str = Field(min_length=1)
    purpose: Literal["tuning", "final", "synthetic"]
    annotation_version: str = Field(min_length=1)
    sources: list[GroundTruthSource] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> "GroundTruth":
        """Prevent repeated sources from multiplying evaluation exposure."""
        for key in ("source_id", "source_sha256"):
            if len({getattr(s, key) for s in self.sources}) != len(self.sources):
                raise ValueError(f"duplicate {key}")
        return self


def canonical_sha256(value: dict) -> str:
    """Identify JSON content independently of whitespace and key ordering."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("time must be a finite nonnegative number")
    if not math.isfinite(value) or value < 0:
        raise ValueError("time must be a finite nonnegative number")
    return float(value)


def _interval(value: dict) -> tuple[float, float]:
    start, end = _seconds(value["start_seconds"]), _seconds(value["end_seconds"])
    if end <= start:
        raise ValueError("interval must have positive duration")
    return start, end


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _duration(intervals: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in _union(intervals))


def _intersection(
    left: list[tuple[float, float]], right: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    return [(max(a, c), min(b, d)) for a, b in left for c, d in right if max(a, c) < min(b, d)]


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _configuration(configuration: dict) -> dict:
    return {k: v for k, v in configuration.items() if k not in {"id", "created_at"}}


def _validate_run(run: dict, source: GroundTruthSource, synthetic: bool) -> list[str]:
    """Reject contradictory identities even when synthetic legacy facts are unknown."""
    _interval(run)
    if run["state"] not in {"queued", "running", "succeeded", "failed", "cancelled", "skipped"}:
        raise ValueError("unknown run state")
    if run["source"]["id"] != source.source_id or run["video_id"] != source.source_id:
        raise ValueError("source identity mismatch")
    duration = run["source"].get("duration_seconds")
    if duration is not None:
        duration = _seconds(duration)
        if (
            duration == 0
            or run["end_seconds"] > duration
            or any(o.end_seconds > duration for o in source.observations)
        ):
            raise ValueError("window/observation exceeds known source duration")
    config = run["configuration"]
    if config.get("schema_version") not in {None, 1, 2}:
        raise ValueError("incompatible configuration schema")
    if config["id"] != run["configuration_id"]:
        raise ValueError("configuration identity mismatch")
    unknown = []
    prepared = run.get("prepared_input")
    if prepared is None:
        unknown.extend(["prepared_input_unavailable", "source_hash_unverified"])
    else:
        if prepared["source_sha256"] != source.source_sha256:
            raise ValueError("source hash mismatch")
        if (
            prepared["id"] != run["prepared_input_id"]
            or prepared["video_id"] != source.source_id
            or _interval(prepared) != _interval(run)
        ):
            raise ValueError("prepared input identity mismatch")
        for key in ("id", "bundle_sha256", "source_sha256"):
            if not re.fullmatch(r"[a-f0-9]{64}", prepared[key]):
                raise ValueError("invalid prepared hash")
        if not prepared.get("preprocessing_version"):
            unknown.append("preprocessing_version_unknown")
        sampling = config.get("preprocessing", {})
        for prepared_key, config_key in (
            ("frame_count", "frames"),
            ("fps", "fps"),
            ("size", "resize"),
            ("preprocessing_version", "version"),
            ("bundle_sha256", "bundle_sha256"),
        ):
            actual, configured = prepared.get(prepared_key), sampling.get(config_key)
            if prepared_key in {"fps", "size"} and actual is None:
                unknown.append(f"prepared_{prepared_key}_unknown")
            if actual is not None and configured is not None and actual != configured:
                raise ValueError("prepared input/configuration sampling mismatch")
        frames = prepared["frames"]
        if len(frames) != prepared["frame_count"] or not frames:
            raise ValueError("prepared frame count mismatch")
        if [_seconds(f["actual_seconds"]) for f in frames] != sorted(
            _seconds(f["actual_seconds"]) for f in frames
        ):
            raise ValueError("prepared frame timestamps unordered")
        for index, frame in enumerate(frames):
            if frame["index"] != index:
                raise ValueError("prepared frame index mismatch")
            _seconds(frame["actual_seconds"])
            _seconds(frame["requested_seconds"])
            if not isinstance(frame["source_pts"], int) or isinstance(frame["source_pts"], bool):
                raise ValueError("invalid source PTS")
            if any(not re.fullmatch(r"[a-f0-9]{64}", frame[k]) for k in ("sha256", "jpeg_sha256")):
                raise ValueError("invalid frame hash")
    prediction = run.get("prediction")
    if (run["state"] == "succeeded") != (prediction is not None):
        raise ValueError("prediction/state mismatch")
    if prediction is not None:
        if prediction["label"] not in ACTIVITY_LABELS:
            raise ValueError("invalid prediction label")
        timestamps = prediction["sampled_timestamps"]
        if not timestamps:
            raise ValueError("empty prediction timestamps")
        times = [_seconds(t) for t in timestamps]
        if times != sorted(times):
            raise ValueError("prediction timestamps unordered")
        if prepared and timestamps != [frame["actual_seconds"] for frame in prepared["frames"]]:
            raise ValueError("prediction/prepared timestamps mismatch")
        for key in ("backend_kind", "model", "fixture_version"):
            if prediction.get(key) != config.get(key):
                raise ValueError("prediction/configuration backend/model/fixture mismatch")
        if prediction.get("provenance") != run["provenance"]:
            raise ValueError("prediction provenance mismatch")
    expected = {"mock": "simulated", "vllm": "online_unverified"}.get(
        config.get("backend_kind"), "unknown"
    )
    if run["provenance"] != expected:
        raise ValueError("configuration provenance mismatch")
    required = ("model", "prompt_text", "prompt_preset", "backend_kind", "schema_version")
    if any(config.get(k) is None for k in required):
        unknown.append("configuration_legacy_unknown")
    for group, keys in (
        ("preprocessing", ("frames", "fps", "resize", "crop")),
        ("generation", ("temperature", "max_tokens")),
    ):
        if any(config.get(group, {}).get(k) is None for k in keys):
            unknown.append(f"{group}_legacy_unknown")
    if not synthetic:
        if expected != "online_unverified" or (
            prediction is not None and run.get("input_status") != "recorded"
        ):
            raise ValueError("real report requires online provenance and recorded input")
        if run["source"].get("source") not in {"upload", "dataset"}:
            raise ValueError("real report rejects synthetic or unknown source provenance")
        required_unknown = [
            fact
            for fact in unknown
            if prediction is not None
            or fact not in {"prepared_input_unavailable", "source_hash_unverified"}
        ]
        if required_unknown:
            raise ValueError("real report rejects unknown legacy identity")
    return unknown


def evaluate(ground_truth: dict, exports: list[dict], *, synthetic: bool = False) -> dict:
    """Compute protocol-v1 metrics without changing supplied predictions or identities."""
    if type(ground_truth.get("schema_version")) is not int:
        raise ValueError("ground truth schema_version must be integer 1")
    truth = GroundTruth.model_validate(ground_truth)
    if synthetic != (truth.purpose == "synthetic"):
        raise ValueError(
            "synthetic mode requires synthetic purpose; real mode requires tuning/final"
        )
    sources = {source.source_id: source for source in truth.sources}
    runs: list[dict] = []
    coverage: list[dict] = []
    unknown = []
    export_hashes = []
    seen_runs: set[str] = set()
    configuration = None
    segment_configuration = None
    for export in exports:
        if (
            type(export["schema_version"]) is not int
            or export["schema_version"] != 1
            or export["activity_labels"] != list(ACTIVITY_LABELS)
        ):
            raise ValueError("incompatible export schema/taxonomy")
        if export["kind"] not in {"run", "session"}:
            raise ValueError("invalid export kind")

        for run in export["runs"]:
            if run["id"] in seen_runs:
                raise ValueError("duplicate run identity")
            seen_runs.add(run["id"])
            if run["video_id"] not in sources:
                raise ValueError("export source absent from ground truth")
            missing = _validate_run(run, sources[run["video_id"]], synthetic)
            unknown.extend({"run_id": run["id"], "fact": fact} for fact in missing)
            saved = _configuration(run["configuration"])
            if configuration is not None and configuration != saved:
                raise ValueError("mixed configurations/backends are not one evaluation")
            configuration = saved
            runs.append(run)
        for item in export["coverage"]:
            _interval(item)
            coverage.append(item)
        export_hashes.append(canonical_sha256(export))
        for segment in export["segments"]:
            saved = segment["configuration"]
            if segment_configuration is not None and segment_configuration != saved:
                raise ValueError("mixed session segment configurations")
            segment_configuration = saved
            expected = {"mock": "simulated", "vllm": "online_unverified"}.get(
                saved.get("backend_kind"), "unknown"
            )
            if segment["provenance"] != expected or (
                not synthetic and expected != "online_unverified"
            ):
                raise ValueError("invalid session segment provenance")
            if configuration is not None:
                for key, value in saved.items():
                    if configuration.get(key) != value:
                        raise ValueError("mixed session segment configurations")
        if export["kind"] == "session":
            if export["source"]["id"] not in sources:
                raise ValueError("session source absent from ground truth")
            if export["session"]["video_id"] != export["source"]["id"]:
                raise ValueError("session source identity mismatch")
            if any(r["video_id"] != export["source"]["id"] for r in export["runs"]):
                raise ValueError("session contains conflicting run source")
            segment_ids = {segment["id"] for segment in export["segments"]}
            for item in export["coverage"]:
                _seconds(item["available_seconds"])
                if item["segment_id"] not in segment_ids:
                    raise ValueError("coverage references absent segment")
                if item["job_id"] is not None and item["job_id"] not in {
                    r["id"] for r in export["runs"]
                }:
                    raise ValueError("coverage references absent run")
    if (
        segment_configuration is not None
        and configuration is not None
        and any(configuration.get(k) != v for k, v in segment_configuration.items())
    ):
        raise ValueError("mixed session segment configurations")
    if not synthetic:
        verified_sources = {r["video_id"] for r in runs if r.get("prepared_input") is not None}
        if verified_sources != set(sources):
            raise ValueError("real report requires verified source hash for every annotated source")
    if not exports:
        raise ValueError("at least one export required")
    confusion = {actual: dict.fromkeys(ACTIVITY_LABELS, 0) for actual in ACTIVITY_LABELS}
    binary = {"true_positive": 0, "false_negative": 0, "false_positive": 0, "true_negative": 0}
    excluded = []
    events = []
    alarms = []
    exposure = dict.fromkeys(
        (
            "observation_seconds",
            "labeled_seconds",
            "processed_observation_seconds",
            "processed_labeled_seconds",
        ),
        0.0,
    )
    for source in truth.sources:
        observations = [(i.start_seconds, i.end_seconds) for i in source.observations]
        annotations = [(i.start_seconds, i.end_seconds) for i in source.annotations]
        source_runs = [r for r in runs if r["video_id"] == source.source_id]
        processed = _union([_interval(r) for r in source_runs if r["prediction"] is not None])
        exposure["observation_seconds"] += _duration(observations)
        exposure["labeled_seconds"] += _duration(annotations)
        exposure["processed_observation_seconds"] += _duration(
            _intersection(processed, observations)
        )
        exposure["processed_labeled_seconds"] += _duration(_intersection(processed, annotations))
        positives = [
            interval
            for observation in observations
            for interval in _union(
                _intersection(
                    [
                        (a.start_seconds, a.end_seconds)
                        for a in source.annotations
                        if a.label in POSITIVE
                    ],
                    [observation],
                )
            )
        ]
        source_events: list[dict] = []
        for index, (start, end) in enumerate(positives):
            covered = _duration(_intersection(processed, [(start, end)]))
            event = {
                "source_id": source.source_id,
                "event_index": index,
                "start_seconds": start,
                "end_seconds": end,
                "processed_seconds": covered,
                "coverage": "uncovered"
                if covered == 0
                else "complete"
                if covered == end - start
                else "partial",
                "matched_run_id": None,
                "detection_delay_seconds": None,
            }
            source_events.append(event)
        source_alarms: list[dict] = []
        for run in source_runs:
            prediction = run["prediction"]
            if prediction is None:
                continue
            anchor = prediction["sampled_timestamps"][0]
            actual = next(
                (a.label for a in source.annotations if a.start_seconds <= anchor < a.end_seconds),
                None,
            )
            if actual is None:
                excluded.append(
                    {"run_id": run["id"], "reason": "unannotated_anchor", "anchor_seconds": anchor}
                )
                continue
            predicted = prediction["label"]
            confusion[actual][predicted] += 1
            key = (
                "true_positive"
                if actual in POSITIVE and predicted in POSITIVE
                else "false_negative"
                if actual in POSITIVE
                else "false_positive"
                if predicted in POSITIVE
                else "true_negative"
            )
            binary[key] += 1
            if predicted in POSITIVE:
                source_alarms.append(
                    {
                        "run_id": run["id"],
                        "source_id": source.source_id,
                        "anchor_seconds": anchor,
                        "evidence_seconds": prediction["sampled_timestamps"][-1],
                    }
                )
        for alarm in sorted(source_alarms, key=lambda a: (a["evidence_seconds"], a["run_id"])):
            event = next(
                (
                    e
                    for e in source_events
                    if e["start_seconds"] <= alarm["anchor_seconds"] < e["end_seconds"]
                ),
                None,
            )
            alarm["classification"] = (
                "false_alarm"
                if event is None
                else "redundant"
                if event["matched_run_id"] is not None
                else "matched"
            )
            if event is not None and event["matched_run_id"] is None:
                event["matched_run_id"] = alarm["run_id"]
                event["detection_delay_seconds"] = (
                    alarm["evidence_seconds"] - event["start_seconds"]
                )
            alarms.append(alarm)
        events.extend(source_events)
    false_alarms = sum(a["classification"] == "false_alarm" for a in alarms)
    missed = sum(e["matched_run_id"] is None for e in events)
    delays = [
        e["detection_delay_seconds"] for e in events if e["detection_delay_seconds"] is not None
    ]
    exposure["unprocessed_seconds"] = (
        exposure["observation_seconds"] - exposure["processed_observation_seconds"]
    )
    return {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "protocol_settings": {
            "timebase": TIMEBASE,
            "intervals": "half_open",
            "window_truth": "first_actual_sample_annotation",
            "confusion_weight": "one_per_successful_labeled_raw_window",
            "event_definition": "maximal_adjacent_fall_fallen_within_observation_v1",
            "alarm_aggregation": "none_raw_windows_v1",
            "matching": "anchor_inside_event_earliest_last_sample_then_run_id",
            "redundant_alarms": "reported_not_false_alarms",
            "delay": "last_actual_sample_minus_event_start",
            "false_alarm_denominator": "union_processed_labeled_window_seconds",
            "coverage": "union_successful_requested_window_intervals",
            "zero_denominator": "null",
        },
        "activity_labels": list(ACTIVITY_LABELS),
        "report_kind": "synthetic_harness_not_model_quality"
        if synthetic
        else "online_quality_proposed_protocol_not_research_validated",
        "ground_truth": ground_truth,
        "ground_truth_sha256": canonical_sha256(ground_truth),
        "export_sha256": export_hashes,
        "configuration": configuration,
        "unknown_identity_facts": unknown,
        "raw_exports": exports,
        "raw_runs": runs,
        "raw_coverage": coverage,
        "confusion_counts": confusion,
        "fall_fallen_binary_counts": binary,
        "excluded_windows": excluded,
        "unprocessed_run_counts": {
            state: sum(r["state"] == state and r["prediction"] is None for r in runs)
            for state in sorted({r["state"] for r in runs if r["prediction"] is None})
        },
        "events": events,
        "alarms": alarms,
        "metrics": {
            **exposure,
            "unprocessed_fraction": _ratio(
                exposure["unprocessed_seconds"], exposure["observation_seconds"]
            ),
            "event_count": len(events),
            "missed_events": missed,
            "missed_event_fraction": _ratio(missed, len(events)),
            "uncovered_events": sum(e["coverage"] == "uncovered" for e in events),
            "partially_covered_events": sum(e["coverage"] == "partial" for e in events),
            "false_alarm_windows": false_alarms,
            "false_alarm_windows_per_processed_labeled_hour": _ratio(
                false_alarms * 3600, exposure["processed_labeled_seconds"]
            ),
            "false_alarm_windows_per_observation_hour": _ratio(
                false_alarms * 3600, exposure["observation_seconds"]
            ),
            "mean_detection_delay_seconds": _ratio(sum(delays), len(delays)),
        },
    }


def _load_json(path: Path) -> dict:
    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(result, dict):
        raise ValueError("JSON manifest must be an object")
    return result


def main() -> None:
    """Write deterministic evaluation JSON from saved exports and explicit annotations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--export", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args()
    try:
        if args.output.resolve() in {path.resolve() for path in [args.ground_truth, *args.export]}:
            raise ValueError("output must not overwrite an input manifest")
        truth = _load_json(args.ground_truth)
        exports = [_load_json(path) for path in args.export]
        report = evaluate(truth, exports, synthetic=args.synthetic)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"Evaluation rejected: {exc}\n")


if __name__ == "__main__":
    main()
