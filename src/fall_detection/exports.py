"""Versioned, allowlisted experiment exports: proposed application behavior."""

import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path

from fall_detection.preparation import load_prepared_input
from fall_detection.repository import Repository
from fall_detection.storage_lock import storage_lock
from fall_detection.taxonomy import ACTIVITY_LABELS

SCHEMA_VERSION = 1


def provenance(backend: str | None) -> str:
    """Distinguish simulation, saved online identity and unknown legacy serving."""
    return {"mock": "simulated", "vllm": "online_unverified"}.get(backend, "unknown")


def fields(row: sqlite3.Row, names: str) -> dict:
    """Select explicit public fields, never paths, claim tokens or payloads."""
    return {name: row[name] for name in names.split()}


SAFE_REASONS = {
    "process_restart",
    "preparation_interrupted",
    "expired",
    "pause",
    "stop",
    "seek",
    "restart",
    "configure",
    "lifecycle_changed",
    "backlog",
    "overload",
    "busy",
    "end_of_source",
    "superseded",
    "incomplete_tail",
}


def safe_reason(value: str | None) -> str | None:
    """Keep lifecycle codes and suppress freeform diagnostics that may include secrets."""
    return value if value is None or value in SAFE_REASONS else "diagnostic_redacted"


def saved_configuration(value: dict) -> dict:
    """Keep saved inference fields and missing legacy fields without runtime defaults."""
    result = {
        name: value[name]
        for name in ("model", "prompt_preset", "prompt_text", "backend_kind", "fixture_version")
        if name in value
    }
    for group, names in (
        ("preprocessing", "frames fps resize crop version bundle_sha256"),
        ("generation", "temperature max_tokens"),
    ):
        if group in value:
            result[group] = {
                name: value[group][name] for name in names.split() if name in value[group]
            }
    return result


class ExperimentExports:
    """Read all selected records within one SQLite snapshot and storage read lock."""

    def __init__(self, repository: Repository, data_dir: Path) -> None:
        """Bind export reads to the application's existing durable stores."""
        self.repository = repository
        self.data_dir = data_dir

    def _run(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        result = fields(
            row,
            "id video_id configuration_id prepared_input_id state start_seconds end_seconds attempt_count created_at updated_at lease_expires_at",
        )
        result["failure"] = {
            "present": row["error"] is not None,
            "diagnostic_sha256": hashlib.sha256(row["error"].encode()).hexdigest()
            if row["error"]
            else None,
        }
        config = connection.execute(
            "SELECT * FROM configurations WHERE id=?", (row["configuration_id"],)
        ).fetchone()
        result["configuration"] = fields(
            config,
            "id schema_version model prompt_preset prompt_text created_at backend_kind fixture_version",
        )
        result["configuration"]["preprocessing"] = saved_configuration(
            {"preprocessing": json.loads(config["preprocessing_json"])}
        )["preprocessing"]
        result["configuration"]["generation"] = saved_configuration(
            {"generation": json.loads(config["generation_json"])}
        )["generation"]
        result["provenance"] = provenance(config["backend_kind"])
        video = connection.execute("SELECT * FROM videos WHERE id=?", (row["video_id"],)).fetchone()
        result["source"] = fields(video, "id source duration_seconds created_at")
        result["prepared_input"] = None
        result["input_status"] = "synthetic" if video["source"] == "synthetic" else "unknown"
        if row["prepared_input_id"]:
            try:
                prepared = load_prepared_input(self.data_dir, row["prepared_input_id"])
            except ValueError:
                result["input_status"] = "unavailable"
            else:
                if prepared.id != row["prepared_input_id"] or prepared.video_id != row["video_id"]:
                    result["input_status"] = "identity_mismatch"
                else:
                    result["prepared_input"] = prepared.model_dump()
                    result["input_status"] = "recorded"
        prediction = connection.execute(
            "SELECT * FROM predictions WHERE job_id=?", (row["id"],)
        ).fetchone()
        result["prediction"] = None
        if prediction:
            result["prediction"] = fields(
                prediction,
                "id label backend_kind model fixture_version request_duration_ms total_duration_ms completed_at",
            )
            result["prediction"]["sampled_timestamps"] = json.loads(
                prediction["sampled_timestamps_json"]
            )
            result["prediction"]["provenance"] = provenance(prediction["backend_kind"])
        events = connection.execute(
            "SELECT * FROM attempt_events WHERE job_id=? ORDER BY id", (row["id"],)
        ).fetchall()
        result["attempt_events"] = [
            fields(event, "attempt event occurred_at error_sha256") for event in events
        ]
        starts = {event["attempt"] for event in events if event["event"] == "started"}
        result["attempt_history"] = (
            "complete"
            if starts == set(range(1, row["attempt_count"] + 1))
            else "partial_legacy_unknown"
        )
        return result

    def snapshot(self, kind: str, identity: str) -> dict:
        """Export every selected record without mutating lifecycle or applying UI limits."""
        with storage_lock(self.data_dir, exclusive=False), self.repository._connect() as connection:
            connection.execute("BEGIN")
            result = {
                "schema_version": SCHEMA_VERSION,
                "kind": kind,
                "activity_labels": list(ACTIVITY_LABELS),
                "runs": [],
                "coverage": [],
                "segments": [],
            }
            if kind == "run":
                row = connection.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
                if row is None:
                    raise KeyError(identity)
                result["runs"] = [self._run(connection, row)]
            else:
                session = connection.execute(
                    "SELECT * FROM monitoring_sessions WHERE id=?", (identity,)
                ).fetchone()
                if session is None:
                    raise KeyError(identity)
                result["session"] = fields(
                    session,
                    "id schema_version video_id state generation segment_id position duration duration_verified origin next_sequence stride expiration created_at updated_at recovery_reason",
                )
                source = connection.execute(
                    "SELECT * FROM videos WHERE id=?", (session["video_id"],)
                ).fetchone()
                result["source"] = fields(source, "id source duration_seconds created_at")
                result["session"]["recovery_reason"] = safe_reason(session["recovery_reason"])
                segments = connection.execute(
                    "SELECT * FROM monitoring_segments WHERE session_id=? ORDER BY generation,created_at,id",
                    (identity,),
                ).fetchall()
                for segment in segments:
                    saved = fields(segment, "id generation start_seconds created_at")
                    saved["configuration"] = saved_configuration(
                        json.loads(segment["configuration_json"])
                    )
                    saved["provenance"] = provenance(saved["configuration"].get("backend_kind"))
                    result["segments"].append(saved)
                windows = connection.execute(
                    "SELECT * FROM monitoring_windows WHERE session_id=? ORDER BY generation,sequence,id",
                    (identity,),
                ).fetchall()
                result["coverage"] = [
                    fields(
                        window,
                        "id schema_version generation sequence sequence_end segment_id start_seconds end_seconds available_seconds state reason prepared_input_id job_id created_at",
                    )
                    for window in windows
                ]
                for item in result["coverage"]:
                    reason = item["reason"]
                    item["reason"] = safe_reason(reason)
                    item["diagnostic_sha256"] = (
                        hashlib.sha256(reason.encode()).hexdigest()
                        if reason and safe_reason(reason) == "diagnostic_redacted"
                        else None
                    )
                rows = connection.execute(
                    "SELECT j.* FROM jobs j JOIN monitoring_windows w ON w.job_id=j.id WHERE w.session_id=? ORDER BY w.generation,w.sequence,w.id",
                    (identity,),
                ).fetchall()
                result["runs"] = [self._run(connection, row) for row in rows]
            connection.commit()
        return result


def json_export(snapshot: dict) -> str:
    """Serialize stable ordering without adding volatile export-time metadata."""
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


CSV_FIELDS = [
    "schema_version",
    "record_type",
    "session_id",
    "window_id",
    "generation",
    "sequence",
    "sequence_end",
    "segment_id",
    "run_id",
    "video_id",
    "configuration_id",
    "prepared_input_id",
    "state",
    "reason",
    "diagnostic_sha256",
    "start_seconds",
    "end_seconds",
    "available_seconds",
    "attempt_count",
    "provenance",
    "model",
    "backend_kind",
    "fixture_version",
    "label",
    "sampled_timestamps",
    "source_sha256",
    "bundle_sha256",
    "frames",
    "configuration",
    "attempt_events",
    "attempt_history",
    "failure",
    "created_at",
    "updated_at",
    "completed_at",
    "request_duration_ms",
    "total_duration_ms",
]


def csv_export(snapshot: dict) -> str:
    """Emit separate coverage and run rows; blank labels never mean other."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")

    def write_row(row: dict) -> None:
        for name, value in row.items():
            if isinstance(value, (dict, list)):
                row[name] = json.dumps(
                    value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                )
            elif isinstance(value, str) and value.startswith(
                ("=", "+", "-", "@", "\t", "\r", "\n", "'")
            ):
                row[name] = "'" + value
        writer.writerow(row)

    writer.writeheader()
    session_id = snapshot.get("session", {}).get("id", "")
    for window in snapshot["coverage"]:
        row = {name: window[name] for name in CSV_FIELDS if name in window}
        row.update(
            schema_version=SCHEMA_VERSION,
            record_type="coverage",
            session_id=session_id,
            window_id=window["id"],
            run_id=window["job_id"],
        )
        segment = next(
            (item for item in snapshot["segments"] if item["id"] == window["segment_id"]), None
        )
        if segment:
            row["configuration"] = json.dumps(
                segment["configuration"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
            row["provenance"] = segment["provenance"]
        write_row(row)
    for run in snapshot["runs"]:
        row = {name: run[name] for name in CSV_FIELDS if name in run}
        row.update(
            schema_version=SCHEMA_VERSION,
            record_type="run",
            session_id=session_id,
            run_id=run["id"],
        )
        config = run["configuration"]
        row.update({name: config[name] for name in ("model", "backend_kind", "fixture_version")})
        if run["prediction"]:
            row.update(
                {name: value for name, value in run["prediction"].items() if name in CSV_FIELDS}
            )
        if run["prepared_input"]:
            row.update(
                {
                    name: run["prepared_input"][name]
                    for name in ("source_sha256", "bundle_sha256", "frames")
                }
            )
        write_row(row)
    return output.getvalue()
