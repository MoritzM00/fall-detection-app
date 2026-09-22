# Web frontend

Implemented as a minimal React/TypeScript/Vite application. It supports the synthetic source, uploads, prepared local OmniFall videos, editable analysis windows, timestamped visual frame previews, job submission, status polling, and provenance-rich result display.

The frame strip is currently a browser-side visual preview. Its timestamps match the deterministic sampler, but the simulated inference path still transports the source clip rather than verified decoded/cropped frames.

Next areas: real frame decoding/crop parity, run comparison, monitoring, and status events.

Only call the application API. Do not embed GPU credentials or call vLLM directly. Align predictions with media timestamps and expose stale/error/skipped states explicitly.
