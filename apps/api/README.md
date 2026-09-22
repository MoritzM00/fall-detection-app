# Application API

Implemented with FastAPI in `main.py`. The MVP supports capability discovery, synthetic and uploaded video assets, media playback, queued analysis jobs, and persisted results.

Next areas: sampled-frame previews, monitoring-session controls, server-sent events, and authentication.

Persist jobs before returning acceptance. Keep decoding and inference in the worker. Database-backed records remain authoritative across client reconnects.
