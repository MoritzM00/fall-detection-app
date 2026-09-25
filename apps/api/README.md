# Application API

Implemented with FastAPI in `main.py`. The MVP supports capability discovery, synthetic and uploaded video assets, media playback, queued analysis jobs, and persisted results.

Next areas: sampled-frame previews, monitoring-session controls, server-sent events, and authentication.

Persist jobs before returning acceptance. Keep decoding and inference in the worker. Database-backed records remain authoritative across client reconnects.

## Upload limits

`FALL_DETECTION_UPLOAD_MAX_BYTES` defaults to 512 MiB. `POST /videos` counts actual request bytes before multipart parsing and returns HTTP 413 once the body exceeds the file limit plus 64 KiB for multipart boundaries and headers. The limit works with missing or incorrect `Content-Length`. A file exactly at the configured limit is accepted when its multipart overhead fits within that allowance; larger headers can cause an earlier 413. The upload handler separately enforces the exact file-byte limit while copying to managed storage and removes partial persistent files on failure.

The same application limit applies to local Uvicorn use and to deployments that route requests to this API. No reverse proxy is configured in this repository. If one is added, set its body limit to the configured file limit plus the chosen multipart allowance, or lower if that is the deployment policy. Keep the application limit enabled even behind a proxy.
