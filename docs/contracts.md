# Draft contracts

These remain evolving draft contracts. The MVP implements the video, capability, analysis-job, result, and vLLM-compatible mock portions; monitoring and event delivery are still proposals.

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

Proposed states: queued, running, succeeded, failed, cancelled, skipped. Skipped denotes intentionally unprocessed monitoring coverage. Retries reuse logical job identity with distinct attempt metadata. A failed parse produces no valid activity label.

## API surface

| Operation | Proposed route |
| --- | --- |
| Upload video | POST /videos |
| Read video metadata | GET /videos/{id} |
| Play stored video | GET /videos/{id}/media |
| Request sampled-frame preview | POST /previews |
| Create analysis job | POST /analysis-jobs |
| Read job/result | GET /analysis-jobs/{id} |
| List runs for comparison | GET /videos/{id}/runs |
| Read available presets and served models | GET /capabilities |
| Create monitoring session | POST /monitoring-sessions |
| Read/update session | GET/PATCH /monitoring-sessions/{id} |
| Receive status/result updates | GET /events |

Preview generation may itself become asynchronous. Media playback should support range requests. Event delivery must support recovery through persisted queries. Authentication and ownership apply consistently once introduced.

## Model boundary

Worker sends selected served-model name, prompt, supported video payload, and generation settings to vLLM. The response envelope is JSON, but the model's generated content initially remains the thesis text format: `The best answer is: <class_label>`.

Parse against the exact taxonomy. Reject invalid or ambiguous output explicitly; do not silently substitute `other`. Raw output stays available for debugging.

Per-request settings include generation values and input/prompt configuration. Server startup settings include GPU allocation, parallelism, context limits, and loaded adapters. The UI only offers server-supported capabilities; it cannot load an arbitrary model by sending a model name.
