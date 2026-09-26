# Current and proposed contracts

The MVP implements video assets, prepared inputs, capabilities, persisted analysis jobs/results, immutable configuration, explicit retries, and the vLLM-compatible mock boundary. Monitoring and event delivery remain proposals. The record table also contains future fields, not a claim that every field is currently exposed.

## Records

| Record | Required information |
| --- | --- |
| Video asset | ID, storage key, duration, dimensions, source, content identity |
| Configuration snapshot | ID, schema version, served model identity, optional adapter identity, prompt text and preset version, preprocessing, generation settings |
| Job | ID, asset, selected range, configuration ID, state, attempt count, lease, error, optional session/generation/sequence |
| Prediction | ID, job ID, canonical label, raw response, sampled timestamps, input window, model/configuration provenance, completion time, processing durations |
| Monitoring session | ID, source, state, playback generation, window length, interval, configuration segments, skipped coverage |

Use media-relative timestamps for video alignment and UTC wall-clock timestamps for lifecycle events. Measure durations with a monotonic clock. Report preprocessing, queue wait, inference request duration, and total latency separately where available; HTTP duration is not necessarily pure GPU compute time.

Snapshot resolved configuration before queuing. Include base model revision and serving version where known; record unknown provenance explicitly rather than inventing it. Preserve the actual prompt sent to the model.

Also persist backend kind (`mock` or `vllm`) and mock fixture/scenario version when applicable. Include this provenance in events, results, and exports so simulated predictions remain identifiable.

## Job lifecycle

Implemented transitions are queued → running → succeeded/failed, with explicit failed → queued retry. Worker claims use owner tokens, 90-second leases, and 20-second heartbeats; expired work becomes failed and requires an explicit retry. Startup handles concurrent WAL setup before serializing migrations. Cancelled/skipped are reserved states; no cancellation or monitoring scheduler is implemented. Skipped is intended to denote intentionally unprocessed monitoring coverage. Retries reuse logical job identity and its saved configuration/input. A failed parse produces no valid activity label.

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
| Read baseline prompt, generation defaults and served model | GET /capabilities | Implemented |
| Create monitoring session | POST /monitoring-sessions | Proposed |
| Read/update session | GET/PATCH /monitoring-sessions/{id} | Proposed |
| Receive status/result updates | GET /events | Proposed; UI currently polls |

Analysis creation optionally accepts `model`, `prompt_text`, and `generation: {temperature, max_tokens}`. Omitted values preserve the configured model, baseline prompt, temperature 0, and max tokens 32. The model must be advertised by capabilities. Prompt text must be nonblank and at most 16000 characters; it is saved exactly. The server derives the baseline preset ID for an exact baseline prompt, otherwise `custom`. Temperature is finite in [0, 2]; max tokens is an integer in [16, 4096]. Sampling, prompt, generation, model, and backend identity are snapshotted before queueing. The minimum applies to new requests; historical lower-budget configurations remain readable and unchanged. It avoids predictably undersized requests but does not guarantee against model truncation. No new schema migration is required: these use existing configuration columns.

The browser compares saved jobs with the same source ID and time window. It checks prepared input identity separately, so equal windows with different sampling are not presented as identical frames. Failed runs retain an error and no valid prediction. Comparison does not rerun jobs or overwrite their configuration.

Preview generation may itself become asynchronous. Media playback should support range requests. Event delivery must support recovery through persisted queries. Authentication and ownership apply consistently once introduced.

## Model boundary

Worker sends selected served-model name, prompt, supported video payload, and generation settings to vLLM. The response envelope is JSON. Baseline runs require generated content in the thesis text format: `The best answer is: <class_label>`. Custom-prompt runs also accept exactly one bare canonical activity label, with surrounding whitespace allowed. JSON-shaped model text, extra prose, multiple labels, and unknown labels remain explicit parse failures. This is proposed application experiment behavior implemented in this repository, not a change to or validation of research behavior.

Parse against the exact taxonomy. Reject invalid or ambiguous output explicitly; do not silently substitute `other`. Raw output stays available for debugging.

Per-request settings include generation values and input/prompt configuration. Server startup settings include GPU allocation, parallelism, context limits, and loaded adapters. The UI only offers server-supported capabilities; it cannot load an arbitrary model by sending a model name.
