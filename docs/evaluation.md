# Offline evaluation protocol v1

These are **proposed, versioned operational definitions**, not verified research
behavior or baseline input parity. Fixture correctness is not measured accuracy.
No acceptance targets are set here; real evaluation (#42) requires targets agreed
before final scoring. Dataset directories are never annotations.

Run the CLI against [canonical JSON exports](exports.md):

```sh
uv run python -m fall_detection.evaluation --ground-truth truth.json --export session.json --output report.json
```

`--export` can repeat for disjoint sources with the same dataset/split/purpose and
exact saved configuration. Use `--synthetic` explicitly for harness verification;
its report is labeled `synthetic_harness_not_model_quality`. Default real mode
requires saved online provenance, recorded frame identities, and a complete saved
configuration and verifiable source hashes for every annotated source. Failed or
pending runs without prepared inputs remain unprocessed and explicitly list the
missing input facts; another retained input must verify that source's hash in a
real report. Successful predictions with unknown inputs are rejected. Online
provenance alone does not establish GPU correctness.

## Ground truth schema 1

```json
{
  "schema_version": 1,
  "dataset_id": "hand-fixture-v1",
  "split_id": "fixture",
  "purpose": "synthetic",
  "annotation_version": "manual-v1",
  "sources": [{
    "source_id": "video-id-from-export",
    "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "timebase": "source_relative_seconds",
    "observations": [{"start_seconds": 0, "end_seconds": 10}],
    "annotations": [
      {"start_seconds": 0, "end_seconds": 2, "label": "walk"},
      {"start_seconds": 2, "end_seconds": 4, "label": "fall"},
      {"start_seconds": 4, "end_seconds": 10, "label": "fallen"}
    ]
  }]
}
```

Purpose is `tuning`, `final`, or `synthetic`. One report has one explicit dataset,
split, annotation version and purpose; no automatic split inference. Repeated
source IDs/hashes are rejected. Times are finite, nonnegative seconds from the
same decoded source origin used by exports (not wall time, PTS ticks, or elapsed
inference time). Every interval is positive and half open `[start,end)`.
Observations and annotations are ordered and nonoverlapping; adjacency is allowed.
Each annotation is entirely inside one observation. Known exported source
durations bound windows and declared observations. Unannotated observation gaps
remain unknown, never `other`. Labels must be one of all 16 application labels.
Only exact source ID/hash matches are accepted. Export schema 1 fixes the source
relative timestamp convention; another timebase requires a new protocol/importer.

## Window, event and exposure semantics

1. Window truth is the annotation at the **first actual sampled timestamp**.
   This is an explicit operational policy reflecting the prompt's first-part
   wording, not a claim of research parity. A timestamp on an interval end belongs
   to the next interval. Unlabeled or out-of-observation anchors are excluded and
   listed. Actual nearest-frame times may lie slightly outside requested window
   bounds; they are retained, never clamped to those bounds. Confusion counts are
   unweighted raw successful windows, including
   overlapping windows; all 16 by 16 cells are emitted. Fall/fallen binary errors
   combine those two labels while retaining separate multiclass counts.
2. Events are maximal adjacent annotated `fall`/`fallen` intervals within an
   observation. An annotation gap or nonpositive class separates events. An alarm
   is each raw fall/fallen window whose truth anchor is labeled. Chronological
   matching uses its first actual sampled timestamp inside an event. The earliest
   last-sampled timestamp (then run ID) wins. Further alarms inside that same
   event are reported as redundant, not extra events or false alarms. Alarms whose
   anchor is in a nonpositive annotation are false alarms. No prediction smoothing,
   voting, label replacement or event aggregation modifies raw predictions.
3. Detection delay is `last_actual_sample - event_start` for the winning alarm.
   It measures source-time evidence availability, can extend beyond event end,
   and is neither wall-clock notification latency nor GPU inference latency.
   Unmatched events have null delay. Every annotated event counts in the missed
   denominator, including partially covered and completely uncovered events;
   those coverage states are also reported separately.
4. Processed labeled exposure is the union of successful raw run intervals
   `[start_seconds,end_seconds)` intersected with annotations and observations.
   Overlap/replays do not double count seconds. Confusion counts still count each
   raw run once. False alarms/hour uses **processed labeled exposure**, including
   positive-event time; the numerator is window alarms, not aggregated alarm
   episodes. The report also emits false alarms per declared observation hour,
   declared observation duration, labeled duration, processed observation duration,
   unprocessed duration and fraction. Thus gaps cannot disappear from the report.
   Interval exposure means processed windows, not proof of every frame observed.
5. Failures, pending runs, skipped/expired coverage and unscheduled source tails
   do not generate labels. They remain unprocessed unless covered by a successful
   overlapping run. Coverage records and failure counts are retained separately.
   Completed observation is never inferred from session state or last result.
   Rates/fractions with zero denominators are null, not zero or infinity.

## Identity and reproducibility

The report carries the complete ground-truth manifest and canonical content SHA-256
of every input export, full saved configurations, raw runs/predictions/input
manifests, and coverage records. It retains their original precision and identity.
Ground-truth selection is explicit; never combine tuning and final manifests or
reuse final results to select settings. A report accepts only one exact saved
configuration content (ignoring its database ID/creation time), with matching
prediction backend/model/fixture identity. Configuration IDs remain separately
visible. Mixed backends, differing prompts/settings, unknown provenance and missing
legacy fields are rejected for real reports. Synthetic mode relaxes *known missing
legacy identity* checks, lists them, and still rejects conflicting known identities,
invalid times/labels and source/hash mismatches. Synthetic built-in exports
without prepared manifests use an explicit fixture SHA in ground truth; that hash
cannot be checked against the export and is listed as `source_hash_unverified`.
Use the SHA of retained generated media when decoding disposable fixtures. This
mode does not allow mixing configurations or splits. Inputs are read without mutating exports.
