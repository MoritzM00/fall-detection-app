# Recorded monitoring sessions

This is proposed application behavior, not verified research behavior. No UI is
added here; issue #37 consumes these polling APIs. SQLite schema version 3 adds
version 1 session/window records without rewriting clip jobs/configurations.
Model responses retain all 16 labels; failed/skipped coverage has no prediction.

## API and playback availability

`POST /monitoring-sessions` creates a **paused** session. Supply `video_id`,
`duration_seconds`, optionally `start_seconds` (default 0), `stride_seconds`
(default 2), `expiration_seconds` (default 5), and clip-compatible inference
settings (`frame_count`, `fps`, `size`, `model`, `prompt_text`, `generation`).
Window width is `(frame_count - 1) / fps`, capped at 30 seconds and 32 frames.
Scheduling policy is immutable for a session. Mock fixture identity is discovered
from the serving process and saved with each configuration segment.

The supplied duration is a provisional playback bound. For real media the worker
verifies video-stream duration **before admission** and shortens the bound when
necessary; unavailable duration/source pauses the session with a visible reason.
Verification rechecks the current timeline transactionally; a seek beyond the real
source bound during metadata inspection also recovers paused with a visible reason.
Frame preparation independently rejects ranges beyond decodable media. Synthetic
sources use their known duration and are restricted to mock serving.

`POST /monitoring-sessions/{id}/commands` accepts `action` and optional
`position_seconds`. Clients send the browser's actual media position with
`position`; elapsed clock time never advances playback availability. The client
must send truthful playback positions: the server cannot observe browser playback.
Normal updates cannot regress or exceed the verified playback bound; backward
movement requires `seek`. While running, the worker admits only windows ending at
or before this watermark. Causal preparation selects no frame PTS after the
admitted interval end, including when the ordinary nearest frame is in the future.
Causal bundles have a separate preprocessing version/content identity; their
actual timestamps, RGB/JPEG integrity and configuration hashes follow the existing
clip pipeline.

Use optional `command_id` (1–128 characters) for retries, particularly
`seek`, `stop`, `restart`, and `configure`. The same ID/payload applies once; reuse
with a different payload returns 409. IDs are scoped to a session. Without an ID,
commands express new intentions and generation-changing commands advance again.

- `start` / `resume`: run the existing timeline; stopped sessions require restart.
- `pause`: stopped sessions require restart; otherwise stop admission and drop an unprepared pending candidate as `pause`.
  An already preparing/queued/running attempt may finish; pause followed by resume
  retains generation, position and sequence.
- `seek`: require position, advance generation, begin a segment there, preserve
  running/paused state and reset sequence.
- `stop`: stop and advance generation at the current position.
- `restart`: advance generation and run from explicit position or zero.
- `configure`: supply `configuration` with `video_id` and inference settings.
  Begin an immutable segment at the current position without advancing generation;
  retain sequence uniqueness and rebase the next interval. Source and replay policy
  cannot be edited with configuration. Seek/stop/restart copy current settings into
  their new segment.

Generation/segment changes skip pending/preparing work and cancel queued monitoring
jobs with explicit coverage reasons. A preparation result rechecks lifecycle and
expiration transactionally before creating a job. Running jobs retain history;
`latest_job_id` selects only successful jobs in the current generation **and current
segment**, ordered by highest sequence, never completion order. It may be null
after seek/configuration changes. Paused sessions can still display an eligible
completed result.

`GET /monitoring-sessions` returns the latest 100 retained sessions.
`GET /monitoring-sessions/{id}` returns lifecycle, verified duration, current
configuration and latest eligible job identity. `GET .../{id}/windows` returns
coverage/history pages (`limit` 1–100, default 100; pass the last `cursor` as
`before`). Resolve each `job_id` through the existing analysis-job API to inspect
its state, immutable configuration, prediction and actual timestamps. Monitoring
jobs cannot be retried through the clip endpoint: use a new playback generation.

## Bounds, coverage and fairness

Deploy one API process and the existing polling worker. At most one session runs,
one monitoring job is queued/running globally (including old generations), and
one candidate is pending/preparing per session. Preparation happens synchronously
on the worker, outside API requests, with one decode slot and bounded selected
frames. There is no decoded backlog or new broker. Ordinary clip preparation still
uses its existing API preparation coordinator.

Every worker poll reconciles playback, then gives queued clip jobs priority before
starting replay preparation or claiming replay inference. A clip arriving during
preparation/inference waits for that current attempt; replay cannot keep inserting
work ahead of queued clips. Sustained clip load can delay replay, intentionally.

If multiple windows became available since the previous tick, schedule only the
newest one; record older windows as a compressed `superseded` coverage row with
inclusive `sequence`/`sequence_end`. Its interval spans those scheduled windows;
stride spacing does not claim continuous inference coverage. Previously pending
candidates displaced by newer windows are `expired`. A candidate expires when its
media lag or controlled-clock age exceeds `expiration_seconds`; queued jobs are
also skipped before claim, and preparation rechecks expiry after decoding. Records
are unique on session/generation/sequence, so repeated polls do not duplicate work.
At EOF, the first stride-aligned interval shorter than a complete window is an
explicit `incomplete_tail`. Other reasons include lifecycle commands,
`process_restart`, source/preparation errors and `preparation_interrupted`.

A preparing candidate older than 90 seconds is fenced and its session pauses when
a worker reconciles it, including while already paused. Resume can then continue
the same timeline without the abandoned candidate; inference has the existing lease/heartbeat and request
timeout. This is a recovery fence, not a forced decoder cancellation. One unusually
slow current decode can delay clip work; input size/frame/window bounds limit
storage/concurrency, not a hard decode deadline. The single worker cannot reconcile
its own hung decode until it returns or restarts.

Only API lifespan startup owns global recovery: running sessions recover paused,
pending/preparing/queued work receives explicit restart coverage, and existing
running inference remains lease fenced. Repository initialization or an additional
worker initialization does not alter sessions. Resume is explicit. A restarted
worker can reconcile abandoned preparation using the 90-second fence. Multiple API
processes/reload restarts are unsupported for uninterrupted replay, as each API
startup intentionally pauses sessions.

Retention protects sources for **all retained sessions**, including paused/stopped
sessions without any jobs, and bundles referenced by window/job history. Publication
and durable job references share the storage lock. No session deletion API is
provided in this issue; retaining history conservatively retains its inputs. Disk
history follows existing manual retention practices; admission bounds are not a
quota on total historical bundles.
