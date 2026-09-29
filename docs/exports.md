# Experiment exports (schema version 1)

This is proposed application behavior, not verified research behavior or evaluation
results. Download the selected run or complete monitoring session from the UI, or:

- `GET /analysis-jobs/{id}/export?format=json|csv`
- `GET /monitoring-sessions/{id}/export?format=json|csv`

JSON is the canonical manifest. Keys are sorted; runs and coverage follow session
generation, sequence and window ID; segments follow generation, creation time and
ID. No export-time timestamp is added, so an unchanged snapshot serializes identically.
Session exports include all generations and records, with no recent-history limit.
CSV contains distinct `coverage` and `run` rows; coverage is not a prediction.
Missing predictions have blank labels. All 16 labels remain unchanged, including
`other`, which never stands for processing failures or skipped coverage.

Saved configuration includes the exact prompt, generation and preprocessing
settings, backend, model and fixture identity. Missing legacy settings remain absent;
export does not insert the display API's legacy FPS default. Run configuration IDs,
prepared-input IDs, source and bundle hashes, per-frame pixel/JPEG hashes, source PTS,
requested and actual source-relative timestamps, and prediction timestamps retain
full stored precision. Synthetic inputs are labeled synthetic and mock predictions
simulated. Online provenance means saved online serving identity, not validated GPU
behavior. Legacy provenance is unknown. Unavailable retained input manifests are
explicitly marked unavailable; metadata cannot recreate deleted frames by itself.

Attempt events are persisted transactionally from this release onward: start,
failure, success, lease expiration and retry request. Existing runs retain their
attempt count, but missing past events are `partial_legacy_unknown`, never fabricated.
Each event includes its attempt number and wall-clock time. Request/pipeline timings
are exported when recorded; processing completion is not the activity timestamp.
Failures preserve diagnostic SHA-256 identifiers and presence without copying
freeform exception messages. Known lifecycle skip reasons remain codes; arbitrary
coverage/recovery diagnostics are redacted. Raw model responses, filenames, storage
paths, claim tokens, media bytes, endpoint URLs, environment settings, credentials
and weights are not exported. Configurations use an explicit field allowlist.
User-authored prompts and model/fixture identifiers are intentionally exported;
do not put credentials, private paths or media payloads in those fields.

CSV embeds structured configuration, frame and attempt data as JSON cells. Scalar
strings starting with `=`, `+`, `-`, `@`, tab or carriage return receive a leading
apostrophe to prevent spreadsheet formula execution. Consumers reversing this
escape remove one apostrophe only when it precedes one of those prefixes. Canonical
JSON and embedded JSON identities are unchanged. Standard CSV quoting handles commas,
newlines and quotes; UTF-8 is used. Prefer JSON for automated reproduction.

Export holds a shared storage maintenance lock while reading immutable prepared
manifests and uses one SQLite read transaction for session, windows, jobs, saved
configurations, predictions and attempt events. It does not recover leases or mutate
jobs. Concurrent updates are wholly before or after the snapshot. Downloads are
materialized in memory, so memory and response size grow with the selected session;
there is no silent truncation. This contract is for local disposable experiments,
not an unbounded streaming or multi-user export service.

To reproduce a run, separately retain authorized source media matching its SHA-256,
the compatible preprocessing implementation/version, the saved prompt and settings,
and the matching model/backend (or deterministic mock scenario and fixture version).
Verify prepared bundle/frame hashes and actual timestamps before inference. Exports
do not bundle media, weights, adapters, deployment secrets or inference-server
settings such as injected mock delay. Obtain those separately from the operator;
legacy/unknown inputs or provenance may prevent exact reproduction. Mock timings
include local serving delay and are not GPU capacity measurements.
