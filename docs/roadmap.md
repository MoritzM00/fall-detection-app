# Implementation roadmap

Updated 27 September 2026 after reviewing `334a231`. This is the current delivery plan; the [review remediation plan](review-remediation-plan.md) is historical. Research sources remain unchanged. All new behavior below is proposed application behavior, not verified research behavior.

## Decision: continue without a GPU

Yes: nearly all clip-analysis and recorded-monitoring application workflows can be implemented against local HTTP simulation. Keep decoding, sampling, saved frame transport, parsing, persistence, scheduling, and the UI real; substitute only the model-serving boundary. Do not build a second mock-only application path.

“95%” is a scope target for application functionality, not a measured completion percentage, effort estimate, or model-readiness claim. The remaining GPU work can still reveal substantial transport, preprocessing, latency, and capacity changes. Accuracy, research parity, prompt quality, and real-world fall-detection readiness cannot be established by simulation.

The immediate goal is a complete, reproducible local clip-analysis and monitoring-replay demo, with failure handling and an evaluation harness ready for real outputs. Real cameras, notifications, multi-user operation, and pilot deployment are outside that denominator.

## Repository assessment

| Area | Evidence in the current repository | Assessment |
| --- | --- | --- |
| Clip analysis | Upload/dataset selection, real PyAV decoding, immutable RGB/JPEG bundles, frame preview, exact prompt/generation settings, saved comparisons | Local MVP implemented |
| Temporal integrity | Actual frame timestamps and bundle/configuration identity retained; recent fixes handle non-zero media origins and tail windows | Locally tested; server interpretation remains unverified |
| Job reliability | SQLite migrations, leases/heartbeats, owner fencing, explicit failed-job retry, recovery/history | Implemented; one job at a time per worker process |
| Preparation/storage | Bounded and deduplicated preparation, upload limits, retryable preparation UI, dry-run retention and generated-media benchmarks | Implemented for one API process; retain that deployment limit |
| Mock HTTP service | Fixed canonical label and configurable delay; validates basic message shape | Useful but incomplete: no scripted activity timeline or HTTP fault scenarios; video bytes and `media_io_kwargs` are not fully validated by the service |
| Mock identity | Jobs save backend and a fixture-version string; worker checks its configured version | Partial: service label/delay settings can change independently of that string; version is not yet verified against the serving process |
| Monitoring | Product/architecture proposals only; no session API, scheduler, or replay UI | Main application gap; no GPU dependency |
| Evaluation/export | Live contract validator exists; recent comparison history is bounded to 50 terminal runs | No full experiment archive or continuous-evaluation harness yet |
| Real serving | Worker uses online video chat-completions transport; opt-in live validator | Implemented client, unverified target service; [issue #9](https://github.com/MoritzM00/fall-detection-app/issues/9) remains open |
| Delivery | Python, frontend, browser and repository-hygiene CI workflows exist | Strong local foundation; local passing checks do not establish deployed behavior |

Verification on 27 September: `make check` passed with 176 Python tests, 13 frontend tests, Ruff lint/format, ty, e2e type checking, and the production build. The initial sandbox run blocked a local socket bind; the rerun with localhost access passed. Existing FastAPI/Starlette deprecation warnings remain. `make browser-test` could not start because localhost:5173 was already occupied; no browser flows were rerun and the existing service was left untouched. No GPU request was made. GitHub showed issue #9 as the sole open issue and no open PRs at assessment time.

## Delivery sequence without GPU access

Each numbered item is a small feature-branch/PR boundary. Preserve all 16 labels, timestamps, immutable input/configuration identity, and explicit processing failures throughout. Never map errors or skipped windows to `other`. Extend existing modules and migrations; PostgreSQL, SSE, a message broker, and a new inference abstraction are not prerequisites.

### 1. Strengthen the mock contract and fixture identity

- Validate the actual prepared JPEG sequence and supported video metadata at the HTTP boundary, including malformed/missing metadata, frame count, and unsupported fields. Keep the existing synthetic demo explicitly distinct from prepared-video fixtures. This validates the application's declared subset, not compatibility with every vLLM release.
- Add a versioned scenario manifest containing label/output, delay, and fault behavior. Derive its identity from the complete effective scenario, including environment overrides. Expose identity through mock-only service discovery and verify it before execution; save it with each run. A queued run must fail clearly if the required fixture is unavailable.
- Select fixtures by stable request/input fingerprints, excluding volatile IDs. Map generated replay windows through fixture manifests rather than global request arrival order. Persist logical attempt state only for intentional fail-then-succeed scenarios. Keep mock control fields out of the production inference body.
- Cover every canonical label, standing → fall → fallen sequences, malformed envelopes, invalid/ambiguous output, truncated answers, HTTP 429/5xx, timeout, and service interruption. Keep explicit job retry as the default; do not introduce automatic inference resubmission here.

Acceptance: the real HTTP client/worker/persistence path exercises success and failure fixtures; repeated/concurrent requests produce the intended stable behavior; stale fixture identity is rejected; failures produce no activity prediction. Mock provenance is visible in every result.

### 2. Persist monitoring sessions and window scheduling

- Add versioned session/window records with source, fixed configuration segment, playback generation, window sequence, media interval, prepared input, and job identity. Enforce unique scheduling per session/generation/sequence.
- Use an injectable clock and explicit replay position. Schedule only fully available windows: no future frame may enter a simulated-live request. Define end-of-file behavior, including an explicit record for an incomplete tail.
- Start with one active replay session, one in-flight monitoring job, and at most one pending window. Make stride and expiration policy explicit saved settings; these are simulation defaults until GPU measurements exist. Record expired/overload windows as skipped coverage rather than silently losing them.
- Define clip-job versus monitoring-job fairness so replay cannot starve submitted clips. Prepare windows through the existing bounded preparation path outside the API request loop; do not accumulate unbounded decoded bundles.
- Pause stops new scheduling; resume continues the same timeline. Stop, seek, and restart advance generation. Configuration changes start a new segment. Keep late completions in history but exclude obsolete generations from the current result. After a process restart, recover the session paused pending explicit resume.

Acceptance: controlled-clock tests prove no lookahead, no duplicate windows, bounded backlog, clip-job progress, and correct pause/resume/seek/stop/restart recovery. Out-of-order results never replace a newer current result. Unprocessed intervals have explicit reasons.

### 3. Complete the monitoring replay interface

- Add recording playback, start/pause/resume/stop, seek, session configuration, and recovery after reload. Keep polling persisted state initially.
- Show the latest eligible prediction alongside its input interval, actual frame timestamps, history, and configuration identity. Preserve the baseline prompt's first-part interpretation; do not portray a prediction as the action at completion time.
- Display processing/queue lag, coverage gaps, failed/disconnected/stale states, and a persistent “Simulated predictions” indicator. Distinguish measured local preprocessing/request time from injected model delay; neither predicts GPU throughput.
- Highlight `fall` and `fallen` without hiding the other 14 labels. Keep activity results separate from system status.

Acceptance: disposable browser flows cover a scripted activity transition, overload, timeout, explicit retry, seek during inference, refresh, and service restart. Old results remain inspectable but cannot appear as current after a generation change.

### 4. Add reproducible exports and evaluation plumbing

- Export session/run manifests as JSON and prediction/coverage tables as CSV, including model/backend, fixture identity, saved configuration, input hashes, source-relative timestamps, failures, skipped windows, and timings. Exclude credentials and media bytes.
- Introduce an explicit ground-truth interval import contract and dataset/split identity. Folder names such as `Fall` and `ADL` are not per-window ground truth.
- Implement metric calculations using hand-checkable synthetic labels: per-class confusion counts, fall/fallen errors, event matching, false alarms per hour, detection delay, and unprocessed coverage. Define matching/timing rules before reporting metrics; retain immutable window predictions if optional event aggregation is added.
- Reject mock or unknown-provenance runs from real-model quality reports by default. Report synthetic fixture results as harness verification only. Keep tuning and final evaluation inputs separate.

Acceptance: known fixtures yield exact expected metrics, coverage gaps and failure denominators are visible, exports retain enough identity to audit/re-run an experiment, and reports cannot silently mix simulated and real predictions.

### 5. Make the GPU handoff and local demo reproducible

- Provide one documented scenario/demo command using generated disposable media and temporary storage. Include nominal, overloaded, disconnected, and recovery scenarios in CI at appropriate test levels.
- Document the supported single-API-process deployment, worker lifecycle, configuration, health checks, and storage/retention operation. Add runtime endpoint authentication support if required by the target service, without persisting secrets in run snapshots.
- Extend the existing live validator only where needed to exercise the same prepared-input fixtures and record target server/model/processor versions. Keep live checks opt-in and separate from normal CPU-only CI.

Acceptance: a fresh CPU-only setup can run all application acceptance scenarios with no SSH, CUDA, model download, or private media; switching to real serving is configuration plus the live integration gate, not a rewrite of application logic.

## GPU and research gates — independent of the sequence above

**G1: Target-service contract validation (blocked on access, issue #9).** Confirm hardware, serving/model/processor versions, network/authentication, supported payload fields, and video metadata interpretation. Run [the existing validator](vllm-live-validation.md) and record evidence. A successful generated-clip response establishes request acceptance and persistence only.

**G2: Research parity and performance (requires G1).** Compare fixed authorized reference inputs with the in-process research baseline, checking exact frames, crop/resize, prompt, temporal metadata, and output differences. Measure real latency, memory, throughput, and saturation; then select monitoring stride, concurrency, and queue limits. Research repository/source changes require separate authorization.

**G3: Model and continuous-behavior evaluation (requires G2).** Use held-out annotated clips/recordings, long negatives, falls crossing boundaries, crop-edge subjects, and multiple people. Agree numerical acceptance targets before final evaluation. Assess per-class quality, missed falls, false alarms per hour, detection delay, and coverage. Simulation cannot satisfy this gate.

GPU access may arrive during any local milestone; run G1 then without stopping unrelated application work. Do not claim that these gates are only 5% of remaining engineering effort.

## Later scope

Camera ingestion/reconnection can be developed with local recorded streams after choosing a protocol, but real-device behavior needs device validation. Notifications, incident acknowledgment, multi-camera operation, access control, retention/deletion policy, deployment ownership, and operational monitoring need a separately scoped pilot plan. PostgreSQL or SSE should follow demonstrated deployment/UI needs rather than block the local replay MVP.

**Next implementation PR:** milestone 1, stronger mock scenarios and verified fixture identity. Follow with session scheduling, then replay UI. The GPU blocker should remain visible without blocking these PRs.
