# Current and proposed contracts

The MVP implements video assets, prepared inputs, capabilities, persisted analysis jobs/results, immutable configuration, explicit retries, and the vLLM-compatible mock boundary. Monitoring and event delivery remain proposals. The record table also contains future fields, not a claim that every field is currently exposed.

## Records

| Record | Required information |
| --- | --- |
| Video asset | ID, storage key, duration, dimensions, source, content identity |
| Configuration snapshot | ID, schema version, served model identity, optional adapter identity, prompt text and preset version, preprocessing, generation settings |
| Job | ID, asset, selected range, configuration ID, state, attempt count, lease, error, optional session/generation/sequence |
| Prediction | ID, job ID, canonical label, raw response, sampled timestamps, input window, model/configuration provenance, completion time, processing durations, server-reported prompt/completion token counts (null when not reported) |
| Monitoring session | ID, source, state, playback generation, window length, interval, configuration segments, skipped coverage |

Use media-relative timestamps for video alignment and UTC wall-clock timestamps for lifecycle events. Measure durations with a monotonic clock. Report preprocessing, queue wait, inference request duration, and total latency separately where available; HTTP duration is not necessarily pure GPU compute time.

Snapshot resolved configuration before queuing. Include base model revision and serving version where known; record unknown provenance explicitly rather than inventing it. Preserve the actual prompt sent to the model.

Also persist backend kind (`mock` or `vllm`) and mock fixture/scenario version when applicable. Include this provenance in events, results, and exports so simulated predictions remain identifiable.

## Job lifecycle

Implemented transitions are queued → running → succeeded/failed, with explicit failed → queued retry. Worker claims use owner tokens, 90-second leases, and 20-second heartbeats; expired work becomes failed and requires an explicit retry. Lease recovery runs when the worker claims work, at API startup, and on an API timer every 15 seconds, so an expired job shows as failed within about 15 seconds even when no worker is running. Read endpoints never recover leases or take a write lock; until recovery runs, a read can still report an expired job as running. Startup sets WAL once (the mode persists in the database file) and handles concurrent WAL setup before serializing migrations. Cancelled/skipped are reserved states; no cancellation or monitoring scheduler is implemented. Skipped is intended to denote intentionally unprocessed monitoring coverage. Retries reuse logical job identity and its saved configuration/input. A failed parse produces no valid activity label.

## API surface

| Operation | Route | Status |
| --- | --- | --- |
| Upload video | POST /videos | Implemented |
| Read video metadata | GET /videos/{id} | Implemented |
| Play stored video | GET /videos/{id}/media | Implemented |
| Prepare sampled frames | POST /prepared-inputs | Implemented, synchronous and bounded |
| Create analysis job | POST /analysis-jobs | Implemented |
| Read job/result | GET /analysis-jobs/{id} | Implemented |
| Retry failed job | POST /analysis-jobs/{id}/retry | Implemented |
| Recent runs for recovery/comparison | GET /analysis-jobs?limit=50 | Implemented; active jobs plus bounded terminal history |
| Read research prompt presets, their sampling defaults and served model | GET /capabilities | Implemented |
| Create monitoring session | POST /monitoring-sessions | Proposed |
| Read/update session | GET/PATCH /monitoring-sessions/{id} | Proposed |
| Receive status/result updates | GET /events | Proposed; UI currently polls |

Analysis creation, monitoring sessions and live sessions optionally accept `model`, `prompt_text`, `chain_of_thought` and `generation: {temperature, max_tokens, top_p, top_k, seed}`. `/capabilities` lists the research presets in `prompt_presets` (see [research baseline](research-baseline.md)), each with its exact prompt, `chain_of_thought` flag and sampling values. The first, `research-zeroshot-v1`, is the default. An omitted prompt or generation resolves to the preset with the requested `chain_of_thought` value, so `{"chain_of_thought": true}` alone selects `research-zeroshot-cot-v1` with its sampling. The model must be advertised by capabilities. Prompt text must be nonblank and at most 16000 characters; it is saved exactly. The server derives a preset ID when both the prompt and `chain_of_thought` exactly match a preset, otherwise `custom`. Temperature is finite in [0, 2]; max tokens is an integer in [16, 4096]; `top_p` is in (0, 1]; `top_k` is -1 (disabled) or 1–1000; `seed` is a non-negative 32-bit integer. A null `top_p`, `top_k` or `seed` is omitted from the request, so the server applies its own default. Runs saved before these fields existed read back with null values and `chain_of_thought: false`. Sampling, prompt, answer format, generation, model, and backend identity are snapshotted before queueing. The minimum applies to new requests; historical lower-budget configurations remain readable and unchanged. It avoids predictably undersized requests but does not guarantee against model truncation. `chain_of_thought` is a configuration column added by schema migration 7; the generation fields live in the existing generation JSON.

Proposed application behavior, not research-verified: when `FALL_DETECTION_MAX_MODEL_LEN` is set, clip jobs, new replay and live monitoring sessions and `configure` commands are rejected with 422 if a conservative estimate of their tokens exceeds it. The estimate adds Qwen3-VL video tokens (`ceil(frames / 2) × (max(ceil(size / 32), 12)² + 16)`), the prompt counted as one token per UTF-8 byte, 32 chat-template tokens and `max_tokens`. It overestimates, so a request close to the window may be rejected although the server would accept it. `/capabilities` reports the window as `serving_limits.max_model_len` (`null` when unset), and the web client blocks submission with the same estimate. The static field limits above are unchanged.

The browser compares saved jobs with the same source ID and time window. It checks prepared input identity separately, so equal windows with different sampling are not presented as identical frames. Failed runs retain an error and no valid prediction. Comparison does not rerun jobs or overwrite their configuration.

Preview generation may itself become asynchronous. Media playback should support range requests. Event delivery must support recovery through persisted queries. Authentication and ownership apply consistently once introduced.

## Model boundary

Worker sends selected served-model name, prompt, supported video payload, and generation settings to vLLM. The response envelope is JSON. Preset runs require generated content in the thesis text format: `The best answer is: <class_label>`. Custom-prompt runs also accept exactly one bare canonical activity label, with surrounding whitespace allowed. JSON-shaped model text, extra prose, multiple labels, and unknown labels remain explicit parse failures. This is proposed application experiment behavior implemented in this repository, not a change to or validation of research behavior.

Proposed application behavior, stricter than the research CoT parser: with `chain_of_thought`, the response may contain reasoning before the answer, either inside `<think>…</think>` (the opening tag may come from the chat template) or as plain text. Only the last nonblank line after any closing tag is parsed, by the same rules as above. An unclosed `<think>`, a response with no line after the reasoning, or a final line in any other format fails explicitly. The research parser instead searches the text after the reasoning for the first answer phrase or any label and falls back to `other`. The full response, including reasoning, is saved as the raw response.

Parse against the exact taxonomy. Reject invalid or ambiguous output explicitly; do not silently substitute `other`. Raw output stays available for debugging.

Per-request settings include generation values and input/prompt configuration. Server startup settings include GPU allocation, parallelism, context limits, and loaded adapters. The UI only offers server-supported capabilities; it cannot load an arbitrary model by sending a model name.
