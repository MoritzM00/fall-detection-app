# Decisions and open questions

## Agreed direction

- Two modes: clip analysis with editable settings and monitoring.
- Qwen3-VL-8B-Instruct base model first; optional local GPU-server adapter later.
- GPU-side vLLM service called by a worker.
- Local mock HTTP serving allows application development without a GPU connection.
- Keep the 16-class taxonomy including ADL.
- The first implemented slice uses SQLite locally, a synthetic sample, and a separate vLLM-compatible mock service. These are explicit MVP substitutions, not validated research behavior.

## Proposed engineering defaults

- React/TypeScript frontend, FastAPI backend, separate Python worker.
- Shared prediction package in one repository.
- PostgreSQL records and job queue; filesystem media storage initially.
- HTTP requests and server-sent events.
- Development GPU connection through SSH forwarding. Implemented instead: a private, scale-to-zero Modal endpoint over HTTPS with proxy auth ([deployment](../deploy/README.md#modal-gpu-endpoint)); a loopback SSH tunnel remains supported.
- Clip analysis before monitoring replay, then camera ingestion.
- Text model output parsed into validated application JSON.

These defaults may change based on implementation evidence.

## Open during implementation

- Extended scripted mock scenarios and monitoring fixtures beyond fixed labels/delays and boundary tests.
- Research reference clips and verification of prompt/preprocessing/inference parity. The prompt presets reproduce the research builder text and sampling values exactly, but message order and server-side sampling defaults still differ (see [research baseline](research-baseline.md)); matching text is not evidence of equivalent predictions.
- Evidence for workloads beyond the generated-media preparation benchmark envelope. Current formats are MP4/MOV/WebM/MKV; uploads default to 512 MiB and selected windows to 30 seconds.

The local process arrangement, repository home, API isolation, bounded preparation, retention command, leases/retries, browser coverage, and prompt/generation editing with saved-run comparison are implemented. PostgreSQL and status events remain proposed. Custom prompts retain all 16 output labels at the parser boundary; processing failures never become `other`.

## Open before monitoring or pilot

- Window interval, tolerated delay, concurrency, queue expiry, and fairness between interactive and monitoring jobs.
- First intended user and environment; numerical quality targets.
- Camera protocol and browser playback approach. Proposal: browser-sampled frames into a server-side frame log ([live camera](live-camera.md)); backend ingest and browser capture implemented locally; not yet agreed.
- Multi-person expectations and crop policy.
- Media/debug-output retention and deletion policy.
- Whether and how predictions become events, notifications, or review tasks.

## Real GPU integration

Resolved for the Modal deployment on 12 October 2026 ([live validation](vllm-live-validation.md#live-run-modal-l4-2026-10-12)):

- One NVIDIA L4 (24 GB), vLLM 0.30.0, and the launch configuration in [`deploy/modal_vllm.py`](../deploy/modal_vllm.py).
- HTTPS with Modal proxy auth, and the saved-JPEG frame-sequence transport with `media_io_kwargs` video metadata.

Still open: research input parity and measured capacity (#41), and aligning the accepted sampling and token limits with the serving context window (#77). These questions do not block local development against mock serving.
