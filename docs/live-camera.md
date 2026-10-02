# Live camera monitoring

Status: **phases 1–2 (backend ingest and browser capture) implemented locally
against mock serving; raw-frame retention is not yet built.** This is proposed application behavior, not verified
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

## Contracts (phase 1, implemented)

### Session creation

`POST /monitoring-sessions/live` creates a **paused** live session. The body takes
the usual inference settings plus capture policy (defaults shown):

```json
{
  "frame_count": 16, "fps": 7.5, "size": 448,
  "model": "…", "prompt_text": "…", "generation": { "temperature": 0, "max_tokens": 32 },
  "stride_seconds": 2, "expiration_seconds": 5,
  "capture_fps": 15, "max_seconds": 3600
}
```

- `capture_fps` (1–30) and `max_seconds` (up to 4 h) are immutable session policy,
  like stride and expiration. The sampling `fps` must be `≤ capture_fps`, at
  creation (422) and for every later `configure` (409).
- The live source is a `videos` row with `source = "live"` whose storage key is the
  frame-log directory. Jobs therefore keep an ordinary `video_id`, and exports list
  the source as `live`. Clip analysis (`/prepared-inputs`, `/analysis-jobs`) and
  recorded sessions reject live sources with 422; there is no media to stream.
- The session stores `source_kind = 'live'` and `capture_fps`; its `duration` is
  `max_seconds`, so the existing end-of-bound handling pauses a session that
  reaches the cap.

Deviation from the first draft: a separate endpoint replaces a `source`
discriminator on `POST /monitoring-sessions`, and the source is a `videos` row
rather than a separate record, because jobs and sessions already reference
`videos(id)`.

### Frame ingest

`POST /monitoring-sessions/{id}/frames` is multipart: a `run_id` form field, a
`metadata` form field with `[{ "run_seq": 41, "capture_seconds": 2.733 }, …]` and
one `frames` file part per JPEG, in the same order. Batches of 1–64 frames are
expected every 250–500 ms.

- A **capture run** is one continuous capture by one page, from Start or Resume
  until it stops. The client picks a new `run_id` for each run, numbers frames
  from `run_seq` 0 and measures `capture_seconds` from the run start on its own
  monotonic clock (`requestVideoFrameCallback` metadata where available, otherwise
  `performance.now()`). The client never needs the server's timeline.
- The server places a run on the session timeline when its first frame arrives:
  the first run starts at the current position; a later run starts after the
  previous run's last frame by the time elapsed on the **server** clock since that
  frame was received (at least one capture interval). This placement is a
  server-side estimate; it is what keeps a reload or a long pause from making
  frames minutes apart look adjacent.
- Within a batch `run_seq` is consecutive and `capture_seconds` strictly increases.
  Each frame must be a readable JPEG of at most 2 MiB and 4096 px per edge.
  Malformed batches return 422.
- Idempotent on `(run_id, run_seq)`: a resent frame with identical time and bytes
  is acknowledged without change, even after the session paused or a newer run
  began, so client retries settle. Conflicts return 409: a stored `run_seq` with
  other content, a new run not starting at `run_seq` 0, a batch that skips
  `run_seq` values (the message names where to resend from), new frames for a run
  superseded by a newer one, new frames while the session is not running, and
  frames past `max_seconds`.
- The response is `{accepted, run_seq_high, watermark_seconds, state}`.

### Watermark and gaps

Each accepted batch sets the session position to its newest placed capture time;
`position` and `seek` commands, and `restart` with an explicit position, return 409
for live sessions. Before storing a batch the API runs one admission pass, so
windows completed before a gap are scheduled first.

Every new run, and any jump of more than `2 / capture_fps` within a run (or from
the grid origin to the first frame), writes one `skipped` coverage row with reason
`ingest_gap` from the first unscheduled window start to the new frame, consumes one
sequence number, and restarts the window grid at that frame. No admitted window
spans missing frames, and a capture failure never becomes an activity (including
`other`). Pause/resume and reload/resume therefore always leave an explicit gap.

Expiration keeps its meaning: a candidate expires when the watermark has moved
more than `expiration_seconds` past its end. Server receive times are stored per
frame so upload latency can later be reported separately from inference lag.

### Preparation

`prepare_frames` reads the frame log, selects for each sampled timestamp the
nearest frame **not after** the window end (earlier frame on ties), verifies each
stored JPEG against its recorded SHA-256, and applies the same center crop and
resize as video preparation. A sampled timestamp without a frame within
`2 / capture_fps` is a defensive `ingest_gap` skip, not a failure. The result is an
ordinary prepared bundle with preprocessing version `pillow-live-frames-jpeg-v1`,
actual capture times as `actual_seconds`, frame `seq` values as `source_pts`, and
the usual RGB/JPEG integrity, so the worker's inference path is unchanged and jobs
keep their input timestamps and configuration identity.

### Lifecycle

- `start` / `resume`: the client starts capturing and uploading; admission follows
  the server watermark. Starting also resets the ingest timeout.
- `pause`: the client stops uploading (frames are not buffered for later); the gap
  is recorded when capture resumes.
- `stop`: as today. `restart`: new generation beginning at the current live edge.
- Reload, tab close or `getUserMedia` track end: when a running session receives no
  frames for `expiration_seconds`, the worker pauses it with recovery reason
  `capture_ended` and skips its unprepared candidate. Resume is explicit, matching
  replay's reload behavior.
- Process restart recovery is unchanged: running sessions recover paused.

## Storage, retention and privacy

Camera frames are personal data of whoever is in view. Proposed defaults, all to
be confirmed:

- Frame logs live under `FALL_DETECTION_DATA_DIR/live/<source id>/` (already
  git-ignored via `data/`; never committed). **Not yet implemented:** raw frames
  are currently kept until the data directory is cleaned manually, and
  `scripts/prune_media.py` does not touch `live/`. Until phase 3, use live
  sessions only with test footage of yourself.
- Raw frames older than `expiration_seconds + window width + margin` are deleted
  by the worker unless a prepared bundle for an admitted window references them.
  Prepared bundles referenced by jobs are retained, as for other sources, because
  they are the prediction's input provenance.
- `max_seconds` caps per-session storage; the existing manual
  [retention](retention.md) command is extended to live bundles.
- Before anyone other than the developer is on camera, the conditions in
  [product](product.md) apply: intended users, consent, access control and
  deletion must be defined first. This slice is a local development and demo tool.

## Browser capture (phase 2, implemented)

- **Monitoring → New session** offers **Recording** or **Camera**. Camera mode asks
  for permission on **Connect camera**, shows the local preview, and creates the
  session with **Create live session** (capture fixed at 15 fps for now).
  Permission denial and missing devices are shown as camera errors, not session
  states.
- The viewer shows the local camera stream with a **Live** marker while running.
  The coverage timeline is a rolling two-minute span ending at the live edge, with
  no scrubber. The header clock, playhead and result lag follow the server
  watermark.
- Start, Resume and Restart connect the camera first if needed. Each running
  period is one capture run (`useLiveCapture`): frames are drawn to a canvas
  (short edge at most 720 px), JPEG-encoded synchronously so `run_seq` stays
  contiguous, and uploaded in ordered batches of up to 32 every 250 ms. Transient
  upload errors back off and retry the same frames. A backlog over ten seconds, or
  a lost-continuity conflict, abandons the run and starts a new one, so the server
  records the gap. "Not running", "superseded" and the length cap stop capture.
- Status adds a **Camera** pill: off, connected, or achieved capture rate and upload
  lag. A **Live camera** badge sits next to the backend badge; mock provenance stays
  visible.
- Reload behaves like recordings: the running session is paused and the camera is
  off until **Resume**, which begins a new run behind an `ingest_gap`.

## Verification plan

- Backend: frame-log ingest (idempotency, ordering, gaps, caps), watermark
  derivation, `ingest_gap` and `capture_ended` coverage, causal frame selection and
  bundle identity, with synthetic frame batches and mock serving.
- Browser (`tests/e2e/live.spec.ts`): Chromium's `--use-fake-device-for-media-stream`
  and `--use-fake-ui-for-media-stream` provide a deterministic fake camera, so the
  suite needs no hardware. It covers connect, create, start, a prediction from
  captured frames with their timestamps, an advancing live clock, pause, reload,
  resume with an explicit `ingest_gap`, and mobile overflow. Upload retry under
  network loss is not yet covered by a browser test.
- No claim about real-model quality or GPU latency on camera input follows from
  these tests.

## Phasing

1. Backend (done): live source, frame ingest endpoint, watermark/gap handling,
   `prepare_frames`, schema migration (SQLite `user_version` 5), tests.
2. Frontend (done): camera capture and upload loop, live viewer and rolling
   timeline, e2e test with the fake camera.
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
