# Local mock inference

## Purpose

Develop clip analysis, configuration editing, result comparison, monitoring, queue behavior, and error handling entirely locally. A separate mock HTTP process substitutes for vLLM. The rest of the application follows its real execution path, including video preprocessing and output parsing.

No model inference occurs. Mock labels and timings are simulated and cannot establish accuracy, GPU capacity, prompt quality, or video-processing parity.

## Switching endpoints

Deployment configuration supplies backend kind (`mock` or `vllm`), base URL, served model name, request timeout, and any credentials. These are planned configuration concepts, not implemented environment variables.

Local mode requires no SSH tunnel, CUDA runtime, model download, or GPU connection. Real mode uses the GPU endpoint. Use the same HTTP client and request serializer for both. Switching environments changes connection configuration; frontend and pipeline logic stay shared.

Persist backend kind and mock scenario/fixture version in run provenance. Show a visible “Simulated predictions” indicator in mock sessions, comparisons, and exports. Keep model identity separate from backend kind. Never mix mock results into real-model quality reports or automatically substitute them after connection failures.

## HTTP contract scope

- `GET /v1/models`: discover the configured served-model alias.
- `POST /v1/chat/completions`: accept the application's actual model, messages, video payload, and supported generation parameters; initially non-streaming only.
- Return an API envelope containing assistant message content such as `The best answer is: fall`, completion identity, and finish reason. Any usage counters included are explicitly synthetic.
- Validate required fields, model name, and the supported payload shape. Reject unsupported inputs rather than quietly accepting a contract the real server may reject.
- Match the error shapes/statuses used by the selected serving version where fixtures cover them. This is a tested subset, not a complete vLLM emulator.

Finalize exact multimodal request serialization during implementation. A mock can validate structure but cannot prove the real server interprets frames, crop settings, or timestamps identically.

## Deterministic scenarios

| Scenario | Behavior to exercise |
| --- | --- |
| Fixed valid label | Basic upload-to-result flow; cover all 16 labels |
| Scripted activity sequence | Monitoring transitions such as standing → fall → fallen |
| Variable delays | Loading states, lag, queue bounds, out-of-order completion |
| Timeout or connection interruption | Retry, reconnect, and explicit failure states |
| Transient HTTP failure | Bounded retries and idempotent persistence |
| Invalid label or malformed envelope | Parse/protocol errors remain separate from `other` |
| Truncated generated answer | Incomplete generation handling |

Select scenarios through local service fixtures/configuration, not by changing production prompts or adding mock-only fields to the inference body. Key scripted responses by a stable input/request fingerprint rather than global arrival order. Exclude volatile IDs and normalize transport details when forming the fixture key. Include logical attempt state only for deliberately scripted retry failures. This makes concurrent jobs and reruns reproducible.

For monitoring fixtures, map known input windows to scripted labels. For arbitrary uploads, use a configured fixed response and clearly identify it as simulated. Prompt-setting edits still travel through the full pipeline but need not produce semantically meaningful mock changes.

## Validation boundary

Use local contract checks to exercise serialization, response parsing, HTTP errors, and request lifecycle. Later run the same client against real vLLM and review differences using fixed reference clips. Sanitize any captured response fixtures and omit private media/credentials.

The existing research `mock_vllm.py` may inform scenarios, but it mocks an in-process engine and does not replace this HTTP boundary. No research code is copied at this stage.
