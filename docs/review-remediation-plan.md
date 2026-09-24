# Review remediation implementation plan

Implement the five broader findings from the 23 September review in a single PR, in coherent commits. Preserve the current visual design and the simple FastAPI/SQLite/worker architecture, all 16 labels, actual frame timestamps, mock provenance, and existing prepared inputs. Do not modify research sources or real runtime data. Do not merge the PR.

## 1. Submitted runs and editable selections

Keep selection state independent of submitted run state. Editing sampling or changing source must never clear an active run. Show the submitted source/window with its result even when a different clip is selected. Keep polling outstanding work independently of the selected clip; prevent accidental duplicate submission while an active tracked run exists. Add a bounded recent-jobs API and a small recent-runs UI so refresh does not lose work. Select/recover jobs from persisted records, not untrusted localStorage data alone. A failed poll must be visible and retryable without deleting the run.

Acceptance: delayed run survives sampling edits, source switching, and refresh; old results are clearly tied to the old source/window. Add frontend behavioral tests for these transitions and backend tests for history ordering/limits.

## 2. Interrupted worker recovery

Add explicit versioned SQLite migration(s) and serialize initialization so API/worker startup cannot race schema changes. Claims carry a unique attempt token and lease expiry. Heartbeat while a job is processing, and stop the heartbeat reliably. Expired claims transition to failed with an actionable interruption message; do not automatically reissue a possibly paid model request. Add explicit retry of failed jobs, reusing job/configuration/input identity and incrementing attempt count on claim. Only the current unexpired owner may complete/fail/renew an attempt. A stale completion must not insert a prediction or overwrite a newer attempt. Keep terminal transitions atomic and get_job reads consistent.

Acceptance: simulate process death/expiry and recovery using controlled clocks rather than long sleeps; a live heartbeat is not reclaimed; stale completion/failure/heartbeat cannot affect a retry; duplicate retries are rejected; migration preserves legacy jobs/predictions and concurrent initialization works. Legacy running jobs with no claim become visibly interrupted, not stuck forever.

## 3. One immutable run configuration

Introduce small validated configuration models for sampling/preprocessing and generation plus model, prompt, backend kind, and mock fixture identity where applicable. Snapshot at queue time, expose with job detail, and execute the saved values. Preserve old model/prompt/generation/preprocessing snapshots through migration. Legacy backend provenance is unknown: document an explicit conservative policy rather than inventing historical facts. New jobs bind the backend kind; if the worker deployment cannot execute that kind, fail clearly instead of silently switching. Do not persist credentials. Endpoint authentication remains runtime configuration.

Carry synthetic frame count/FPS/size through the request and snapshot, deriving the same timestamps shown in preview. Real-video settings come from the validated prepared manifest, never contradictory client fields. Populate model/backend presentation from capabilities and the submitted job. Remove unused duplicate contract definitions when made obsolete. Generation values must be loaded from the snapshot, not new runtime defaults.

Acceptance: queue, change runtime defaults, then process and verify the original payload/settings; custom model display; non-default synthetic frame count; prepared settings mismatch rejected; malformed inference envelopes/timeouts/HTTP failures result in explicit processing failure.

## 4. Frontend simplification

Canonical sampling state is start/count/FPS/size; derive end. Editing end recalculates FPS. Use one source-selection reset path. Extract focused source, sampling, preview, and run/result components and hooks for preparation and job polling where it reduces responsibility; avoid arbitrary tiny wrappers or a new global state framework. Capabilities loading failure must not silently claim mock inference is configured. Keep errors and loading states truthful. Cover short clips, invalid windows, rapid edits, polling/preparation failures and active-run behavior with a minimal frontend test setup.

## 5. Bounded preparation and storage

Keep synchronous preparation for this MVP unless evidence requires a new asynchronous service. Add bounded concurrent preparation and a bounded waiting policy: excess distinct requests get a clear retryable response rather than unlimited decoding. Deduplicate identical in-flight requests before expensive work; handle concurrent publication portably. Coordinate across supported API processes or explicitly enforce/document a single API-process scope. Browser aborts may suppress obsolete requests but are not a claim that server-side decoding is cancelled.

Add a configurable upload byte limit checked while copying, reject oversized uploads with 413, and remove partial/orphan files when copying or metadata insertion fails. Preserve source/hash/timestamp integrity. Benchmark generated representative early/late windows and cache hits; record measurements and choose conservative optimizations. Do not replace content validation with a weak stat-only cache. Seek optimization is optional only if exact nearest-PTS behavior is demonstrated for relevant formats.

Keep RGB and JPEG verification. Make optional inspection PNG generation an explicit setting, preserving legacy bundle readability; do not silently delete existing files. Define retention and provide a safe manual cleanup command with dry-run default, explicit apply flag, age threshold and protection for assets/bundles referenced by any retained job (including queued/running/failed retryable jobs). Prevent concurrent preparation/upload cleanup races and traversal/symlink escapes. Run cleanup tests only on disposable fixtures, never user data. If implementing full cleanup safely is disproportionate, document manual retain-all policy and the unimplemented portion explicitly rather than claim it is solved.

Acceptance: concurrent duplicate requests perform one preparation; capacity excess is bounded; partial upload and DB failure cleanup; oversized upload rejected; current frame integrity tests retained; pruning cannot remove referenced/fresh/in-flight resources.

## Validation and delivery

Read AGENTS.md and relevant Python skills. Existing local checkout at /Users/moritz/projects/fall-detection-app has installed .venv and apps/web/node_modules if useful; ensure tests import THIS worktree, not the original editable installation. Prefer isolated dependency environments. No runtime data should be copied. Use appropriately scoped tools for missing dependencies.

Run Ruff lint/format, ty, full pytest, frontend behavioral tests, and production build. Exercise a mock HTTP end-to-end flow if feasible. Record live-GPU validation as not performed. Make small commits. Push only codex/review-remediation and create one PR to main with a cohesive final-scope description and actual validation results. The parent has pushed existing local commits to origin/main at 0853b7e, so the PR should contain only this remediation. Normal Git credentials are stale; use git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin codex/review-remediation without changing saved Git configuration. Attach the created PR to this task. Report worktree path, commits, URL, tests, and any limitations to the parent for independent review. Do not merge or delete the worktree.
