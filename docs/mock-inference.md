# Local mock inference

## Purpose

Develop clip analysis, configuration editing, result comparison, monitoring, queue behavior, and error handling entirely locally. A separate mock HTTP process substitutes for vLLM. Clip analysis exercises real frame decoding, selected-window preprocessing, persistence, media transport, prompt/generation editing, output parsing, and saved-run comparison. Monitoring remains planned.

No model inference occurs. Mock labels and timings are simulated and cannot establish accuracy, GPU capacity, prompt quality, or video-processing parity.

## Switching endpoints

Implemented environment settings include `FALL_DETECTION_BACKEND_KIND`, `FALL_DETECTION_INFERENCE_BASE_URL`, `FALL_DETECTION_INFERENCE_MODEL`, and `FALL_DETECTION_REQUEST_TIMEOUT_SECONDS`. Credential handling remains planned. The worker supports real inference only for prepared videos and rejects synthetic sources on that path. Queued backend identity is enforced; there is no automatic mock fallback.

Local mode requires no SSH tunnel, CUDA runtime, model download, or GPU connection. The real mode path is implemented; its GPU transport compatibility still requires the opt-in live validator in [vllm-live-validation.md](vllm-live-validation.md).

Persist backend kind and mock scenario/fixture version in run provenance. Show a visible “Simulated predictions” indicator in mock sessions, comparisons, and exports. Keep model identity separate from backend kind. Never mix mock results into real-model quality reports or automatically substitute them after connection failures.

## HTTP contract scope

- `GET /v1/mock/identity`: mock-only effective fixture discovery.
- `GET /v1/models`: discover the configured served-model alias.
- `POST /v1/chat/completions`: accept the application's actual model, messages, video payload, and supported generation parameters; initially non-streaming only.
- Return an API envelope containing assistant message content such as `The best answer is: fall`, completion identity, and finish reason. Any usage counters included are explicitly synthetic.
- Validate required fields, model name, and the supported payload shape. Reject unsupported inputs rather than quietly accepting a contract the real server may reject.
- Match the error shapes/statuses used by the selected serving version where fixtures cover them. This is a tested subset, not a complete vLLM emulator.

The current request uses a saved JPEG frame sequence and video metadata described in [prepared-video.md](prepared-video.md). A mock can validate structure but cannot prove the real server interprets frames, crop settings, or timestamps identically.

## Implemented deterministic scenarios

| Scenario | Behavior to exercise |
| --- | --- |
| Fixed valid label | Basic upload-to-result flow; cover all 16 labels |
| Scripted activity sequence | Monitoring transitions such as standing → fall → fallen |
| Variable delays | Loading states, lag, queue bounds, out-of-order completion |
| Timeout or connection interruption | Retry, reconnect, and explicit failure states |
| Transient HTTP failure | Explicit retry and idempotent persistence |
| Invalid label or malformed envelope | Parse/protocol errors remain separate from `other` |
| Truncated generated answer | Incomplete generation handling |

The service loads a version-1 JSON manifest from `MOCK_INFERENCE_MANIFEST`. Without a file it serves the fixed `fall` response with 900 ms delay. `MOCK_INFERENCE_MODEL`, `MOCK_INFERENCE_LABEL`, and `MOCK_INFERENCE_DELAY_MS` override the model and default scenario. All effective fields, including overrides and fingerprint mappings, contribute to `mock-v1-sha256:<digest>`. Restart the service after configuration changes.

```json
{
  "version": 1,
  "model": "qwen3-vl-8b-instruct",
  "default": {"label": "fall", "delay_ms": 900, "fault": "none"},
  "inputs": {},
  "requests": {}
}
```

Each mapping value has the same scenario shape as `default`, with optional `output` and `fail_first` fields. Labels accept exactly the 16 canonical activities. Faults are `none`, `malformed` (missing choices), `invalid`, `ambiguous`, `truncated` (length finish reason), `429`, `500`, `503`, and `interruption` (incomplete response stream). Use a `delay_ms` greater than `FALL_DETECTION_REQUEST_TIMEOUT_SECONDS` to exercise timeouts. A stopped service exercises unavailability. These are simulation behaviors, not evidence of a specific vLLM release's error semantics.

`GET /v1/mock/identity` returns the effective manifest and fixture identity. Submission discovers and snapshots that identity; discovery failure returns 503 without queuing a run. The worker rediscovers it, then sends `X-Mock-Fixture-Identity` only for mock inference. The service checks this header before executing, and the client verifies the response header. This closes the discovery/request race and rejects changed or unavailable fixtures. The production inference JSON is unchanged. The old `FALL_DETECTION_MOCK_FIXTURE_VERSION` environment setting is removed; older queued string identities such as `sample-v1` fail clearly and require a new submission. Existing successful runs retain their original provenance.

Successful responses expose `X-Mock-Input-Fingerprint` and `X-Mock-Request-Fingerprint`. To script generated replay windows, submit each window's actual prepared payload once to the disposable mock, capture its input fingerprint, and map those keys under `inputs` to `standing`, `fall`, and `fallen`. Reload the manifest, then submit new application jobs to snapshot its new identity. This selects by window content rather than arrival order, even with concurrent requests. Request-specific mappings under `requests` take precedence over input mappings; unmapped uploads use `default`.

Mapping keys must be lowercase 64-character SHA-256 digests. Input fingerprints hash decoded JPEG bytes in order plus validated metadata (FPS/duration normalized to floats), with a distinct synthetic-demo marker. Request fingerprints additionally include the served model, exact prompt, and effective generation settings. They exclude application job/video/prepared-input IDs, filenames, base64 spelling, message-part order, and HTTP headers. Identical inputs with identical settings resolve identically. Two byte-identical windows with identical metadata intentionally share a mapping; distinguishing their source times would require different prepared input content or a future production timestamp contract. Saved actual frame timestamps remain in the application's prediction record and are never replaced by response time.

The JPEG validator decodes every frame, checks JPEG format and matching dimensions, and rejects missing/unsupported metadata, inconsistent counts/indices, sampling enabled, and invalid FPS/duration. Only `fps`, `frames_indices`, `total_num_frames`, `duration`, and `do_sample_frames` are supported. The synthetic demo accepts only the application's known synthetic marker and forbids prepared metadata; arbitrary MP4 bytes, remote URLs, PNG frames, and unknown request/message fields are rejected.

Retries remain explicit through the existing failed-job retry endpoint. Ordinary scenarios have no request counters. Deliberate fail-then-succeed scenarios set `fail_first` and a fault, and require `MOCK_INFERENCE_ATTEMPT_DB` pointing to a writable SQLite file outside the repository. Atomic durable counters are keyed by effective fixture identity and request fingerprint. The first N accepted completion attempts emit the configured fault; subsequent attempts emit the scenario output/label. Counters survive service restart and are shared by identical logical requests, including concurrent submissions. A different manifest gets a fresh counter namespace. Changing the database path does not change the manifest identity; keep that database with the fixture when reproducing retry state. Missing state storage fails explicitly. Discovery and rejected identity/payload requests do not consume attempts.

Local HTTP tests cover all labels, concurrent activity transitions, prepared-media transport, discovery and stale identity, parser/envelope failures, HTTP faults, timeout, interruption, durable counters, and explicit worker retry. Failed jobs retain their error and have no activity prediction; `other` remains an ordinary canonical label.

## Validation boundary

Verified locally on 27 September 2026: Ruff lint/format, ty, 230 Python tests, 13 frontend tests, e2e type checking, and production build passed. Nine existing browser flows passed with generated disposable media/storage and isolated ephemeral API/mock/web ports because the default web port was occupied; existing services were left untouched. No GPU or real serving checks ran.

Use local contract checks to exercise serialization, response parsing, HTTP errors, and request lifecycle. Later run the same client against real vLLM and review differences using fixed reference clips. Sanitize any captured response fixtures and omit private media/credentials.

The existing research `mock_vllm.py` may inform scenarios, but it mocks an in-process engine and does not replace this HTTP boundary. No research code is copied at this stage.

Completion requests are bounded before JSON parsing to 32 MiB, including chunked bodies. Prepared inputs accept at most 32 frames, 1 MiB of encoded JPEG bytes per frame, and 672 × 672 decoded pixels per frame. Larger inputs fail with HTTP 413 before full image decoding. JPEG validation runs in the thread pool so it does not block the async event loop. These fixed mock limits match the application's supported preparation range.
