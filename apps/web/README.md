# Web frontend

Implemented as a minimal React/TypeScript/Vite application. It supports the synthetic source, uploads, prepared local OmniFall videos, editable analysis windows, timestamped visual frame previews, job submission, status polling, and provenance-rich result display.

The frame strip displays the saved JPEGs from the prepared input, and submitted runs retain that input identity and its actual frame timestamps. The local mock returns a simulated label; the online vLLM frame contract still needs validation against the GPU service.

Next areas: run comparison, monitoring, and status events.

Only call the application API. Do not embed GPU credentials or call vLLM directly. Align predictions with media timestamps and expose stale/error/skipped states explicitly.

## Full-browser tests

From the repository root, after `make setup`, install the matching browser once and run:

```bash
apps/web/node_modules/.bin/playwright install chromium
make browser-test
```

The runner requires `ffmpeg` with the `libx264` encoder. It generates a three-second MP4 and uses a temporary database and media directory, then starts the local API, worker, mock inference service, and Vite app. No GPU or personal video is needed. Ports 8000, 8001, and 5173 must be free. The browser suite covers upload, video duration, prepared frames, submitted-run identity, reload recovery, retries, and API error states. Failure traces are written under the ignored `apps/web/test-results/` directory.
