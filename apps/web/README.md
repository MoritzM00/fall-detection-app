# Web frontend

Implemented as a minimal React/TypeScript/Vite application. It supports the synthetic source, uploads, job submission, status polling, and provenance-rich result display.

Next areas: editable time ranges, sampled-frame preview, run comparison, monitoring, and status events.

Only call the application API. Do not embed GPU credentials or call vLLM directly. Align predictions with media timestamps and expose stale/error/skipped states explicitly.
