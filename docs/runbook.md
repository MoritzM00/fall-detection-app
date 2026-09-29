# Local application runbook and GPU handoff

The CPU track (#35–#40, tracked in #43) verifies proposed application behavior:
real decoding, saved JPEG transport, persistence, replay scheduling, UI, exports,
and synthetic evaluation. Only model serving is simulated. Research input parity,
real model quality, network compatibility and GPU capacity remain unverified.

## Fresh CPU setup and disposable acceptance

Install Python 3.12+ and uv; `uv sync --locked` prepares the backend. Run:

```sh
uv run python -m scripts.demo
```

No SSH, CUDA, model download, private media, ffmpeg or Node is needed for this
command. PyAV generates a moving pattern in temporary storage. The runner starts
its own API, worker and HTTP mock; it uploads the video through `/videos`, prepares
saved frames through `/prepared-inputs`, submits real durable jobs, polls their
results and downloads canonical run exports. It removes all generated artifacts.
Four JSON lines report `status: passed`, `simulated: true`, the fixture hash and
actual input timestamps, followed by:

```text
PASS: four simulated scenarios; owned children reaped; temporary media/state removed.
```

| Scenario | Acceptance |
| --- | --- |
| nominal | Prepared uploaded video succeeds with mock provenance and preserved input/configuration identity |
| overloaded | Selected HTTP 429 fails without a prediction; explicit retry succeeds with the same identity. A controlled playback jump produces compressed superseded coverage and at most one pending/preparing candidate |
| disconnected | Stop only the owned mock after queuing; discovery times out with no prediction. Restart the same fixture and explicitly retry |
| recovery | Restart only the owned API with retained temporary storage; running replay recovers paused with `process_restart`, then explicitly resumes |

The demo reserves ephemeral loopback sockets and passes them directly to uvicorn;
they remain reserved across its simulated outages. Disconnection therefore models
an endpoint that accepts TCP but cannot answer, rather than connection refusal.
It waits at most 15 seconds for each readiness/result condition and uses a one
second serving timeout. Cleanup terminates, force-kills if needed, and reaps only
owned children on success, exceptions, Ctrl-C and SIGTERM. SIGKILL cannot execute
cleanup. The runner is a bounded smoke command; it does not measure sustained
throughput, continuous accuracy or interactive UI behavior.

For interactive use install Node/pnpm and run `make setup && make dev`, opening
<http://localhost:5173>. State lives in ignored `data/`. For disposable browser
acceptance install ffmpeg and Playwright Chromium, then `make browser-test`.
The browser runner selects distinct ephemeral API/mock/web ports and Vite rejects
a port conflict rather than moving to another port. A bind race fails clearly;
tests never reuse existing servers. Override `FALL_DETECTION_API_PORT`,
`FALL_DETECTION_MOCK_PORT`, `FALL_DETECTION_WEB_PORT` for interactive development;
also set `FALL_DETECTION_INFERENCE_BASE_URL` when changing the mock port.

## Processes, health and configuration

Deploy **one API process and one polling worker**. API startup owns recovery and
pauses running replay; uvicorn reload and multiple API processes are unsupported
for uninterrupted monitoring. The development launcher deliberately omits reload.
API and worker need the same data directory, SQLite database and serving settings.
Keep them on shared local storage; distributed filesystem/process deployments
have not been validated. Shut down the launcher with Ctrl-C; worker termination
allows current work to finish, then the launcher applies its bounded kill/reap.

`GET /health` reports API liveness only. `/capabilities` reports taxonomy and
configured serving kind/model. `/v1/mock/identity` confirms the effective mock
fixture; `/v1/models` discovers serving aliases. A live API does not prove a live
worker or model: inspect queued/running jobs and explicit errors. Worker leases
and heartbeat fence abandoned inference; retry failed clip jobs explicitly.
Replay after API restart requires Resume; monitoring failures require a new
generation via Restart/retry inference, not a clip-job retry. See
[monitoring lifecycle and bounds](monitoring.md) for expiration, the 90-second
abandoned-preparation fence, fairness, source bounds and coverage semantics.

Set `FALL_DETECTION_DATA_DIR`, `FALL_DETECTION_DATABASE_PATH`,
`FALL_DETECTION_BACKEND_KIND` (`mock` or `vllm`),
`FALL_DETECTION_INFERENCE_BASE_URL`, `FALL_DETECTION_INFERENCE_MODEL`, and
`FALL_DETECTION_REQUEST_TIMEOUT_SECONDS` consistently. API upload/preparation
limits are `FALL_DETECTION_UPLOAD_MAX_BYTES`, `FALL_DETECTION_PREPARATION_SLOTS`
and `FALL_DETECTION_PREPARATION_WAIT_SECONDS`. Mock manifest and durable retry
state are described in [mock inference](mock-inference.md). Fixture changes require
new submissions; changed fixture identities cannot silently execute old jobs.
All 16 labels remain valid; failures and skipped windows have no activity class.

Back up the database and managed media together with the application stopped.
Sources, prepared bundles and histories are linked: copying only SQLite loses
inputs. Retention is manual, defaults to preview and protects all retained job
and session references. Follow [retention](retention.md); never prune user data
as part of demo/test cleanup. Histories have no automatic total disk quota.
Export reproducible [run/session manifests](exports.md) before analysis; evaluate
with the [ground-truth protocol](evaluation.md). Synthetic reports test arithmetic
and contracts and cannot demonstrate model quality.

## Real GPU handoff: independent gates

Normal CI runs Python contract/lifecycle tests, the disposable application command,
frontend tests/build and browser flows. Controlled browser outage fixtures verify
UI behavior; actual HTTP failures and restarts above verify the application stack.
GPU validation is opt-in and never part of CPU CI.

When GPU access returns, reuse `scripts/validate_vllm_contract.py` and its generated
fixture rather than changing the prepared-input contract. Follow
[live validator instructions](vllm-live-validation.md). Record the exact repository
commit, UTC run date, validator output/errors, endpoint transport arrangement,
GPU-host vLLM version, served model alias and model revision, processor class,
Transformers version and processor revision, serving command/options, and any
adapter identity. Record identities/version facts, never credentials or weights.
The validator checks saved JPEG bytes, request metadata, parsed label, actual
timestamps, configuration and explicit unavailable-endpoint failure with no mock
fallback. Its local fake-response self-test is not real-service evidence.

Target endpoint authentication requirements are unresolved. No authentication
scheme or credential persistence is introduced here; agree the secured transport
and required auth mechanism with the endpoint owner before external deployment.
Do not place credentials in URLs, manifests, exports or logs. #9 remains the real
transport gate, #41 covers research input parity and measured capacity, and #42
covers held-out continuous evaluation with targets agreed before final scoring.
