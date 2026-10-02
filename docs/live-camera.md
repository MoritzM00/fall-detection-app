# Live camera monitoring (design proposal)

Status: **proposed, not implemented.** This is application design, not verified
research behavior. It addresses the open decision "Camera protocol and browser
playback approach" ([decisions](decisions.md)) and the ingestion part of roadmap
milestone 6. Nothing here changes the research baseline or claims parity with it.

## Goal and scope

Let a monitoring session take its input from a camera instead of a recording,
reusing the existing session, generation, window, expiry and coverage machinery
described in [recorded monitoring](monitoring.md).

In scope for the first slice: one browser camera (`getUserMedia`) on the machine
running the browser, mock serving first, the same 16-label output and the same
coverage reasons. Out of scope: RTSP/IP cameras, multiple cameras, event
aggregation, notifications, and anything presented as pilot-ready.

## What changes relative to replay

Replay has a complete file on disk; the browser only reports a playback watermark
and the server cannot observe playback. A camera has no file: frames must reach
the server as they are captured, and the server can observe availability directly.

| Concern | Recorded replay (today) | Live camera (proposed) |
|---|---|---|
| Source of frames | Uploaded/dataset file decoded with PyAV | Server-side **frame log** appended by an ingest endpoint |
| Availability watermark | Client `position` commands (trusted) | Server-derived: newest contiguously received capture time |
| Duration bound | Verified stream duration | None; bounded by a maximum session length |
| Seek / restart from position | Supported | Not meaningful; restart begins a new generation at the live edge |
| Preview | Browser plays the file | Browser shows its own camera stream; no preview transport |
| Reload / tab close | Session pauses; resume is explicit | Capture ends; session pauses with `capture_ended` |

Admission, `superseded` coalescing, expiration, one-candidate-per-session, the
global one-monitoring-job bound and clip-job priority stay as they are.

## Transport options considered

1. **Contiguous `MediaRecorder` chunks appended to one growing WebM file.** Closest
   to "video in, video decoded". But browser WebM output has no duration or cues,
   so `verify_source` cannot bound it and each window decode would scan an
   ever-growing file. Codec and container output also differ between browsers.
2. **Restarted `MediaRecorder` segments** (self-contained files every few seconds).
   Every restart drops frames at the boundary, so this needs multi-file windows
   and boundary gap accounting.
3. **Browser-sampled frames** (recommended for the first slice). The page draws
   camera frames to a canvas at a fixed capture rate and uploads them as JPEG
   batches with capture timestamps. The server stores them as a frame log;
   preparation selects frames by timestamp. No container parsing, bounded per-window
   cost, and the frame log is also the natural sink for a later server-side RTSP
   reader.

Option 3 trades video-codec fidelity for simplicity. Camera input is out of
distribution relative to the dataset regardless, so the preprocessing identity
must say "live frames" explicitly rather than imply research equivalence.

## Proposed contracts

### Session creation

`POST /monitoring-sessions` gains a source discriminator. Existing requests are
unchanged (`kind: "recording"` is implied by `video_id`).

```json
{
  "source": { "kind": "live", "capture_fps": 15, "max_seconds": 3600 },
  "frame_count": 16, "fps": 7.5, "size": 448,
  "model": "…", "prompt_text": "…", "generation": { "temperature": 0, "max_tokens": 32 },
  "stride_seconds": 2, "expiration_seconds": 5
}
```

- `capture_fps` (1–30) and `max_seconds` are immutable session policy, like stride
  and expiration. The sampling `fps` of every configuration segment must be
  `≤ capture_fps`; `configure` rejects anything higher.
- The session is created **paused**, with `duration = null` and
  `source_kind = 'live'`. A live source record (not a `VideoAsset`) owns the
  frame-log location.

### Frame ingest

`POST /monitoring-sessions/{id}/frames` (multipart) takes one JSON part with
`[{ "seq": 412, "capture_seconds": 27.466 }, …]` and one JPEG part per frame.
Batches are expected every 250–500 ms.

- `capture_seconds` is on a session-relative monotonic clock taken from the
  browser (`requestVideoFrameCallback` metadata where available, otherwise
  `performance.now()`), zeroed at the first captured frame.
- Idempotent on `(session, seq)`: resending a batch after a network error is
  safe. A conflicting payload for an existing `seq` returns 409.
- Rejected: non-increasing timestamps, frames above a size cap, frames while the
  session is not `running` (409, so the client stops uploading), and frames past
  `max_seconds`.
- The response returns the server watermark and the highest contiguous `seq`, so
  the client can show upload lag and resend gaps.

### Watermark and gaps

The worker treats the newest **contiguously received** `capture_seconds` as the
session position; clients cannot send `position` or `seek` for live sessions (400).
A missing `seq` range or a capture-time jump larger than `2 / capture_fps` becomes
an explicit `ingest_gap` coverage row. A window overlapping a gap is skipped with
that reason and has no prediction, so a capture failure never turns into an
activity (including `other`).

Expiration keeps its meaning: a candidate expires when the watermark has moved
more than `expiration_seconds` past its end. Server receive times are recorded per
batch so that upload latency can be reported separately from inference lag; the
two clocks are not assumed to agree.

### Preparation

A new `prepare_frames` path reads the frame log for `[start, end]`, selects for
each target timestamp the nearest frame **not after** the window end (the existing
causal rule), and applies the existing center crop and resize. The output is an
ordinary prepared bundle with its own preprocessing version (`…-live-frames`),
actual capture timestamps, content hash and RGB/JPEG integrity, so jobs keep their
input timestamps and configuration identity exactly as clip and replay jobs do.

### Lifecycle

- `start` / `resume`: the client starts capturing and uploading; admission follows
  the server watermark.
- `pause`: the client stops uploading (frames are not buffered for later); the gap
  is recorded when capture resumes.
- `stop`: as today. `restart`: new generation beginning at the current live edge;
  explicit positions are rejected.
- Reload, tab close or `getUserMedia` track end: the server sees no frames for
  `expiration_seconds` while running, pauses the session with `capture_ended`, and
  resume is explicit, matching replay's reload behavior.
- Process restart recovery is unchanged: running sessions recover paused.

## Storage, retention and privacy

Camera frames are personal data of whoever is in view. Proposed defaults, all to
be confirmed:

- Frame logs live under `FALL_DETECTION_DATA_DIR/live/<session>/` (already
  git-ignored via `data/`; never committed).
- Raw frames older than `expiration_seconds + window width + margin` are deleted
  by the worker unless a prepared bundle for an admitted window references them.
  Prepared bundles referenced by jobs are retained, as for other sources, because
  they are the prediction's input provenance.
- `max_seconds` caps per-session storage; the existing manual
  [retention](retention.md) command is extended to live bundles.
- Before anyone other than the developer is on camera, the conditions in
  [product](product.md) apply: intended users, consent, access control and
  deletion must be defined first. This slice is a local development and demo tool.

## UI sketch

- Source selection gains **Use camera**; permission denial and missing devices are
  shown as source errors, not session states.
- The viewer shows the local camera stream (`srcObject`). The timeline becomes a
  rolling window (for example the last five minutes) with the live edge on the
  right; the scrubber is hidden.
- Status adds achieved capture rate, upload lag and dropped/resent frames next to
  System, Inference and Result lag.
- The backend badge additionally says **Live camera**; mock provenance stays visible.

## Verification plan

- Backend: frame-log ingest (idempotency, ordering, gaps, caps), watermark
  derivation, `ingest_gap` and `capture_ended` coverage, causal frame selection and
  bundle identity, with synthetic frame batches and mock serving.
- Browser: Chromium's `--use-fake-device-for-media-stream` and
  `--use-fake-ui-for-media-stream` provide a deterministic fake camera, so the
  Playwright suite needs no hardware. Cover start/pause/resume, network loss with
  resend, tab reload, and mobile layout.
- No claim about real-model quality or GPU latency on camera input follows from
  these tests.

## Phasing

1. Backend: live source record, frame ingest endpoint, watermark/gap handling,
   `prepare_frames`, schema migration, tests.
2. Frontend: camera capture and upload loop, live viewer and rolling timeline,
   e2e tests with the fake camera.
3. Retention for live frames and documentation updates (`monitoring.md`,
   `contracts.md`, `retention.md`).
4. Later, separately: a server-side RTSP reader writing the same frame log, plus
   the preview transport it would need.

## Open decisions

- Default `capture_fps` (15 proposed) and upload resolution/JPEG quality, balancing
  bandwidth against crop quality at 448 px.
- Raw-frame retention horizon and whether bundles for skipped windows are kept.
- Maximum session length (1 hour proposed).
- Whether a running live session should survive a tab reload by re-acquiring the
  camera automatically, or always pause (proposed: always pause).
