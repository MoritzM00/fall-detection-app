# Code quality review — 23 September 2026

Reviewed the Python domain modules, API, worker, mock server, React interface, development launcher, tests, and project configuration in `/Users/moritz/projects/fall-detection-app`. No research repository, video data, dependency versions, or deployment configuration was changed.

The existing module boundaries are sensible for this MVP. Keep the small FastAPI + SQLite + worker architecture. The largest simplification opportunity is eliminating duplicated state and configuration, rather than adding frameworks, a generic repository layer, or a new queue service.

**Verification:** the original 46 tests passed. After the fixes, all 52 tests pass; Ruff lint and formatting, Python type checks, and the TypeScript/Vite production build pass. Seven additional temporary Node assertions checked frontend response handling. Those assertions are not a committed frontend test suite. Two dependency deprecation warnings remain. No browser interaction test or live GPU/vLLM validation was performed; passing the mock tests does not establish real model compatibility or accuracy.

## Small fixes completed

| Finding | Evidence and fix | Validation |
| --- | --- | --- |
| Prepared-input cache mixed up identical uploads | The cache key used bytes/settings, while its manifest belonged to a specific asset. A second asset with identical bytes received the first asset's manifest and could not create a matching job. Include the asset ID in new cache keys. Existing bundles remain readable; new preparations can create additional bundles. | Reproduced with a failing regression test; now verifies separate identities, identical pixel hashes, and stable cache reuse for both assets. |
| Empty frame arrays crashed the worker | NumPy raised `EOFError`, outside the worker's handled errors, leaving the job running. Translate unreadable arrays into a contextual `ValueError`. | Empty and invalid array tests verify failed state, no prediction, and a worker that can continue polling. |
| Duplicate integrity work | Worker loaded/hashed the RGB bundle, then the JPEG transport function loaded/hashed it again. Removed the redundant worker call; the transport boundary retains validation. | Existing frame-transport tests plus a new corrupted-JPEG test verify inference never receives corrupt bytes. |
| Stable video creation raced | Concurrent create-or-return requests both observed a missing row, then one insert failed. Replaced check-then-insert with targeted `ON CONFLICT(id) DO NOTHING`, followed by a read. This also removes duplicated branches. | Reproduced actual SQLite uniqueness errors for sample and dataset records; concurrent tests now return one identical asset to every caller. |
| Frontend API errors were unreadable | Validation details can be an array, but the client assumed a string and produced `[object Object]`. Extract validation messages and keep a fallback for malformed/non-JSON errors. | Seven response-handling assertions and the production build pass. |
| Small clarity issues | Moved worker logging configuration into its executable entry point and corrected the JPEG endpoint's misleading “lossless” docstring. | Lint, types, and existing tests pass. |

Commits: `fb1837f` (prepared inputs/worker), `0e6bce8` (concurrent video creation), `746d53e` (frontend errors).

## Broader changes to discuss before implementation

### 1. Keep active runs separate from editable selections — P1

Evidence: [App.tsx](/Users/moritz/projects/fall-detection-app/apps/web/src/App.tsx:185), especially `analyze`, `updateRange`, and the frame/FPS/crop handlers. `busy` becomes false as soon as enqueueing finishes. Editing a range or setting then calls `setJob(null)` while the worker continues processing. Polling stops and the old result is no longer reachable through the interface. Changing source and reloading also lose the only job reference. The API has no run-list route to recover it.

Proposed change: separate the current selection from the submitted run. Keep tracking a submitted run until terminal state, even when the next selection is edited. Decide whether the MVP should lock controls during analysis or allow editing while showing the submitted run independently; the latter scales better to comparison. Add recoverable run identity/history if refresh recovery is required.

Scope: frontend state and components, with API/repository work if history is included. Acceptance: edit settings during a delayed run, switch source, and refresh; the original run must remain recoverable and never appear to describe a different selection. This deserves UI behavior tests.

### 2. Recover interrupted jobs — P1, already acknowledged as planned

Evidence: [Repository.claim_next_job](/Users/moritz/projects/fall-detection-app/src/fall_detection/repository.py:263) only claims `queued` rows. After committing `running`, a process kill, machine restart, or unhandled error can leave that job running forever. Graceful signal handling covers normal shutdown only. The array fix closes one failure path, not crash recovery generally.

Proposed change: add claim ownership, an expiry/heartbeat, and a deliberate recovery policy. For the simplest first step, mark abandoned work failed with a retry action; automatic retries require protection against duplicate requests and late completions. Preserve the logical job and configuration identity across attempts. Guard terminal updates by the claim/attempt identity.

Scope: database evolution, repository, worker, API/UI status and retry handling. Acceptance: stop the worker after claiming, restart, and verify deterministic recovery; ensure an old attempt cannot overwrite a newer result. Keep SQLite unless actual deployment needs justify replacement.

### 3. Make one saved configuration drive preview and inference — P2

Evidence: [repository configuration storage](/Users/moritz/projects/fall-detection-app/src/fall_detection/repository.py:190), [configuration loading](/Users/moritz/projects/fall-detection-app/src/fall_detection/repository.py:227), [payload construction](/Users/moritz/projects/fall-detection-app/src/fall_detection/pipeline.py:30), and [frontend controls](/Users/moritz/projects/fall-detection-app/apps/web/src/App.tsx:342).

Current defect: synthetic previews accept an editable frame count, but job creation sends no such setting for synthetic inputs. The repository snapshots 16 frames and the pipeline samples 16 timestamps regardless of the displayed count. A user can therefore preview six synthetic frames and receive a 16-timestamp result. It is explicitly simulated, but its displayed configuration is inconsistent. The model heading also remains Qwen3-VL 8B when the configured served model changes.

Latent reproducibility problem: generation settings are saved but never read back; the pipeline independently hard-codes matching defaults. They agree today. A future default change would make an old queued job execute with different generation values from its snapshot. Backend selection is also taken from the worker environment at execution time rather than the queued configuration.

Proposed change: introduce a small validated run-configuration record used at enqueue and execution. Read saved generation/preprocessing values and define whether a queued run binds its backend. Populate displayed model/capabilities from the API. For the synthetic sample, either lock unsupported settings or carry them through explicitly—this is a product choice, not a silent refactor.

Scope: models, API, repository schema/loader, pipeline, worker, frontend. Acceptance: queue a job, change runtime defaults, then process it; the request and saved configuration must agree. Test non-default synthetic frame counts and custom served-model names.

### 4. Reduce frontend state duplication and component responsibility — P2

Evidence: [App.tsx state](/Users/moritz/projects/fall-detection-app/apps/web/src/App.tsx:22). The entire interface, source acquisition, preview requests, polling, and error handling live in one 391-line component. Start, end, frame count, and FPS are stored independently despite the invariant `end = start + (frames - 1) / fps`. Source-reset sequences are repeated in three handlers.

Proposed change: retain start/count/FPS as canonical sampling state and derive end; editing end updates FPS. Extract source selection, sampling controls, preview, and result display, with focused hooks for preparation and polling. Use one source-selection/reset function. Do not add Redux or a broad generic state abstraction for this scale.

Scope: mainly frontend. Combine with item 1 so state ownership is designed once. Acceptance: source switching, short clips, invalid ranges, rapid edits, failed preparation, failed polling, and active-run tracking behave consistently.

### 5. Bound preparation work and clarify stored artifacts — P2

Evidence: [preparation](/Users/moritz/projects/fall-detection-app/src/fall_detection/preparation.py:92), [frame decoding](/Users/moritz/projects/fall-detection-app/src/fall_detection/preparation.py:26), [preview effect](/Users/moritz/projects/fall-detection-app/apps/web/src/App.tsx:60), and [uploads](/Users/moritz/projects/fall-detection-app/apps/api/main.py:108).

Every preparation hashes the entire source before even checking the cache; a cache miss decodes from the beginning and hashes the source again. Moving a window late into a long recording repeats substantial work. The UI suppresses obsolete responses but does not stop already-running server preparation. Uploads have no application-level byte limit or cleanup on a failed copy/database insert; saved uploads and bundles have no retention policy.

Each bundle stores RGB arrays, PNGs, and JPEGs. This is intentional in the current contract, but the worker and browser use JPEGs while PNGs are mostly for inspection. Storage growth and repeated decoding are predictable from the code; their real-world cost was not benchmarked in this review.

Proposed change: first measure representative long clips, then bound preparation concurrency and deduplicate in-flight work. Consider an asynchronous preparation lifecycle if requests are slow. Evaluate safe seeking without changing nearest-PTS selection, and define upload limits, failed-upload cleanup, and retention. Decide whether PNGs are needed outside an explicit debug/export mode before removing them. Preserve timestamp and integrity guarantees.

Scope: preparation, API, storage lifecycle, UI pending/cancellation handling, and real-media regression fixtures. Acceptance: rapid range changes do not create unbounded work; long-window selection preserves the same selected PTS; failed writes leave no orphan media; jobs retain referenced bundles.

## Test and maintenance priorities

- The tests cover taxonomy parsing, normal persistence, prepared pixels, mock contracts, and selected worker failures well. They do not cover worker crash recovery, browser state transitions, or a real GPU integration. Add tests around the broader changes above instead of expanding assertions about internal helper calls.
- Add inference-client boundary cases for HTTP failure, timeouts, malformed response bodies, and non-text content. Existing preparation tests replace the completion call, while mock tests exercise the mock route separately; those do not validate the whole HTTP path.
- API setup uses module globals and tests patch them. An application factory with explicit settings/repository ownership would simplify isolation when the API grows, but is unnecessary churn for this fix pass.
- SQLite initialization embeds schema detection and migrations in application startup. Concurrent initialization/migration deserves a dedicated test before multiple processes are used against an older database. Introduce explicit schema versions when the next migration is needed.
- `get_job` performs two autocommit reads, so a completion between them can briefly return a pre-completion state with a prediction. A consistent read transaction or one joined query would improve lifecycle consistency; include this in the repository lifecycle work.
- The unused `InferenceRequest` model duplicates part of the mock request shape. Remove or consolidate it when introducing the shared configuration; avoid growing a second unused contract.
- The development launcher should eventually handle partial process-start failures and wait again after killing a timed-out child. These are local development robustness improvements, not reasons to redesign the application runtime.

**Recommended order:** settle active-run UI behavior and simplify its state together; add job recovery; unify configuration; then measure and improve preparation. Larger changes above are proposals and have not been implemented.
