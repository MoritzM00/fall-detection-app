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

- Mock scenarios, fixtures, and supported serving-contract subset.
- Local worker/service hosting arrangement.
- Reference clips and exact initial prompt/generation preset.
- Supported upload formats, size/duration limits, and treatment of long selections.
- Final repository home/name; currently scaffolded inside the project workspace.

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
