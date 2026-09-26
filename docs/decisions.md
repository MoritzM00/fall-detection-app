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
- Development GPU connection through SSH forwarding.
- Clip analysis before monitoring replay, then camera ingestion.
- Text model output parsed into validated application JSON.

These defaults may change based on implementation evidence.

## Open during implementation

- Extended scripted mock scenarios and monitoring fixtures beyond fixed labels/delays and boundary tests.
- Research reference clips and verification of prompt/preprocessing/inference parity. The local named prompt defaults to temperature 0 and max tokens 32; naming alone is not evidence of research equivalence.
- Evidence for workloads beyond the generated-media preparation benchmark envelope. Current formats are MP4/MOV/WebM/MKV; uploads default to 512 MiB and selected windows to 30 seconds.

The local process arrangement, repository home, API isolation, bounded preparation, retention command, leases/retries, browser coverage, and prompt/generation editing with saved-run comparison are implemented. PostgreSQL and status events remain proposed. Custom prompts retain all 16 output labels at the parser boundary; processing failures never become `other`.

## Open before monitoring or pilot

- Window interval, tolerated delay, concurrency, queue expiry, and fairness between interactive and monitoring jobs.
- First intended user and environment; numerical quality targets.
- Camera protocol and browser playback approach.
- Multi-person expectations and crop policy.
- Media/debug-output retention and deletion policy.
- Whether and how predictions become events, notifications, or review tasks.

## Open before real GPU integration

- GPU type/count/memory, installed vLLM version, and working launch configuration.
- Worker-to-GPU connection details and exact video transport compatibility.

These questions do not block local development against mock serving.
