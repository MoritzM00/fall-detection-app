# Fall Detection Product

Local MVP for a video activity-recognition application built on the master's thesis work in `fall-detection-mllm`.

**Status:** clip analysis runs end to end against the default simulated inference backend. Uploaded and dataset videos are decoded into immutable frame bundles before analysis. The online vLLM path is implemented but has not been tested against the GPU server yet.

## Product modes

- **Clip analysis:** upload a clip, select its time range, inspect sampled frames, configure the prompt and inference settings, run inference, and compare runs.
- **Monitoring:** replay a recording as live, process successive windows, and show timestamped activity predictions. Real camera ingestion follows later.

Both modes use the same prediction pipeline and preserve the 16-class taxonomy. Start with Qwen3-VL-8B-Instruct without an adapter on the existing GPU server. Adapter support is a later configuration option.

## Repository outline

| Path | Planned responsibility |
| --- | --- |
| [apps/web](apps/web) | React/TypeScript clip-analysis interface |
| [apps/api](apps/api) | FastAPI upload, capability, and job API |
| [apps/worker](apps/worker) | Asynchronous persisted-job worker |
| [apps/mock_inference](apps/mock_inference) | Local vLLM-compatible HTTP substitute |
| [src/fall_detection](src/fall_detection) | Shared taxonomy, sampling, prompts, inference client, parsing, and persistence |
| [deploy](deploy/README.md) | Future application and GPU serving configuration |
| [tests](tests/README.md) | Planned validation boundaries |

## Planning documents

1. [Architecture and data flows](docs/architecture.md)
2. [Product behavior](docs/product.md)
3. [Draft data and API contracts](docs/contracts.md)
4. [Research baseline and migration](docs/research-baseline.md)
5. [Implementation milestones](docs/roadmap.md)
6. [Decisions and open questions](docs/decisions.md)
7. [Local mock inference](docs/mock-inference.md)

## Run locally

Requirements: Python 3.12+, `uv`, Node.js, and npm.

```bash
make setup
make dev
```

Open <http://localhost:5173>, choose the synthetic sample or a prepared local OmniFall video, and run analysis. Four local processes start: the web UI, application API, worker, and mock inference service. Runtime state is written to the ignored `data/` directory.

```bash
make check
```

The mock returns `fall` after a short delay. Override its deterministic behavior with `MOCK_INFERENCE_LABEL` or `MOCK_INFERENCE_DELAY_MS`. Mock provenance remains attached to every result. Set `FALL_DETECTION_BACKEND_KIND=vllm` and `FALL_DETECTION_INFERENCE_BASE_URL` to use a compatible online vLLM server for prepared real videos. The worker never silently falls back to mock inference.

## Current MVP limits

- The built-in corridor source is a synthetic animated preview, not committed video data.
- Prepared videos under `data/omnifall/videos` are discovered locally and remain ignored by Git. Dataset folder names such as `Fall` and `ADL` are source groupings, not model predictions.
- Uploaded and dataset clips are decoded with PyAV using the selected window, frame count, sampling FPS, and center-crop size. The UI shows the same JPEG frame files sent in the online request; jobs retain the prepared input ID.
- Both mock and real vLLM backends use the online chat-completions `video_url` frame-sequence format. Real inference requires a configured vLLM server with a version that supports request-level video `media_io_kwargs`; it is not GPU-tested here.
- Monitoring, run comparison, retries/leases, and PostgreSQL migration remain planned.

See [prepared video input](docs/prepared-video.md) for the frame and vLLM payload contracts.
