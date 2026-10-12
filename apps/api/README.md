# Application API

Implemented with FastAPI in `main.py`. The MVP supports capability discovery, synthetic and uploaded video assets, media playback, queued analysis jobs, and persisted results.

Sampled-frame previews use `POST /prepared-inputs`. `POST /analysis-jobs` optionally accepts `model`, `prompt_text`, and `generation` (`temperature`, `max_tokens`). Omitted settings retain baseline behavior; model overrides must match the advertised served model. Prompts must contain 1–16000 characters, temperature must be finite and from 0 to 2, and max tokens must be an integer from 16 to 4096. Custom prompt text is saved exactly and marked `custom`; the baseline prompt keeps its preset ID. Custom runs accept either the fixed answer format or one bare canonical activity label; baseline parsing stays strict. The new-request token minimum is conservative and does not guarantee completion; historical snapshots with lower budgets remain readable and unchanged.

Next areas: monitoring-session controls, server-sent events, and authentication.

Persist jobs before returning acceptance. Preparation decodes frames synchronously through a bounded application coordinator; the worker consumes those immutable frames and performs inference. Database-backed records remain authoritative across client reconnects.

For isolated tests or embedded use, call `create_app(settings, repository)`. Each instance owns its configuration and preparation coordinator, and initializes its repository on startup. `apps.api.main:app` remains the Uvicorn entry point.

## Upload limits

`FALL_DETECTION_UPLOAD_MAX_BYTES` defaults to 512 MiB. `POST /videos` counts actual request bytes before multipart parsing and returns HTTP 413 once the body exceeds the file limit plus 64 KiB for multipart boundaries and headers. The limit works with missing or incorrect `Content-Length`. A file exactly at the configured limit is accepted when its multipart overhead fits within that allowance; larger headers can cause an earlier 413. The upload handler separately enforces the exact file-byte limit while copying to managed storage and removes partial persistent files on failure.

The same application limit applies to local Uvicorn use and to deployments that route requests to this API. No reverse proxy is configured in this repository. If one is added, set its body limit to the configured file limit plus the chosen multipart allowance, or lower if that is the deployment policy. Keep the application limit enabled even behind a proxy.

## Request guards

The API has no authentication and is meant to listen on `127.0.0.1` for one developer. `request_guards.py` protects it from web pages open in the same browser:

- **Host allow-list.** Requests whose `Host` is not `127.0.0.1` or `localhost` (any port) get HTTP 400, so a DNS-rebinding page cannot read responses such as prepared frames, source media or exports. `FALL_DETECTION_ALLOWED_HOSTS` adds comma-separated names.
- **Client header.** POST, PUT, PATCH and DELETE requests need `X-Requested-With: fall-detection`, otherwise HTTP 403 before the body is read. A custom header forces a CORS preflight for cross-origin callers, which neither this API nor the Vite dev proxy grants, so another site cannot start a GPU warm-up, upload media or retry jobs with a body-less or multipart POST. GET stays header-free so `<img>`, `<video>` and download links keep working. First-party callers (the web client, `scripts/demo.py`, the tests) send `CLIENT_HEADERS`.
- **No CORS.** The web UI reaches the API through the same-origin Vite proxy, so the API sends no CORS headers. The proxy sets `server.cors: false`, because Vite's default would answer preflights from other localhost pages and let them send the client header through `/api`.
