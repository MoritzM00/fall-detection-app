# Deployment

The only executable deployment configuration is the Modal GPU endpoint described below. The application services themselves still run locally.

Planned application services: web, API, worker, PostgreSQL, and shared media volume. GPU serving remains on the existing separate server.

Default local development adds the mock inference HTTP service and points the worker to it. No GPU connection or weights are required. A real-inference deployment selects the GPU endpoint instead; both use the same client contract. In containers, address the mock by its service hostname rather than worker loopback.

Initial connection plan: GPU-local vLLM listener on port 8000; SSH forwarding from worker host port 8001; worker API base at `http://127.0.0.1:8001/v1`. These are proposed defaults, not an active endpoint. If the worker runs in a container, its loopback is separate from the host: tunnel placement/networking must be configured accordingly.

vLLM will serve `Qwen/Qwen3-VL-8B-Instruct`, optionally under an application alias. Verify hardware/version-specific startup settings before writing the launch configuration. Keep credentials and model/adapter weights outside Git.

Later: process supervision, private networking or HTTPS, secrets injection, health checks, backup/restore, and retention jobs. Server memory/parallelism/loading options are not ordinary browser-editable inference settings.

## Modal GPU endpoint

[`modal_vllm.py`](modal_vllm.py) serves `Qwen/Qwen3-VL-8B-Instruct` with vLLM on a rented Modal GPU. The application keeps running locally (`make dev`), and only the worker talks to the endpoint. The request contract was [validated live on an L4](../docs/vllm-live-validation.md#live-run-modal-l4-2026-10-12) on 2026-10-12; capacity and research parity have not been measured yet.

Cost and access controls:

- **Scale to zero.** The GPU container stops 10 minutes after the last request and is billed per second while it runs. `max_containers=1` means at most one GPU runs at a time. Set a workspace spending limit in the Modal dashboard as a hard cap.
- **Proxy auth.** Modal rejects requests without a valid proxy auth token at its edge, before any container starts, so a request without credentials never starts the GPU.
- **Cold start.** The first request after idling waits for the container to start and the model to load: about 3.5 minutes in the first measured start, including a 74 s `torch.compile` that is now cached. The very first start also downloads about 17 GB of weights into a Modal volume. A later cold start from the web UI's **Warm up** button took 172 s. Warm up when you start a session (button or `make gpu-warm`) so the GPU is ready by the time you submit, and raise `FALL_DETECTION_REQUEST_TIMEOUT_SECONDS` so a window sent during a cold start does not time out.

Setup:

```sh
uvx modal setup                              # once: authenticate the Modal CLI
uvx modal deploy deploy/modal_vllm.py        # prints the endpoint URL
```

Create a proxy auth token in the Modal dashboard (Settings → Proxy Auth Tokens), then run the app against the endpoint:

```sh
export FALL_DETECTION_BACKEND_KIND=vllm
export FALL_DETECTION_INFERENCE_BASE_URL=https://<workspace>--fall-detection-vllm-serve.modal.run/v1
export FALL_DETECTION_INFERENCE_PROXY_KEY=wk-...
export FALL_DETECTION_INFERENCE_PROXY_SECRET=ws-...
export FALL_DETECTION_REQUEST_TIMEOUT_SECONDS=600
export FALL_DETECTION_MODAL_APP=fall-detection-vllm   # GPU status badge
make dev
```

Keep these lines in a Git-ignored file such as `.env` and load it with `source .env`; never commit the token. `make gpu-warm` loads `.env` itself, wakes the endpoint and prints how long it took to become ready. To take the endpoint down entirely, run `uvx modal app stop fall-detection-vllm`.

### GPU status in the web UI

With the vLLM backend, the top bar shows the GPU state instead of a static label: idle (scaled to zero), container up, warming up, ready, or unknown. The API reads it from Modal's control plane (`Function.get_current_stats`) using your local Modal CLI login, caches it for 5 seconds, and **never sends a request to vLLM**, so an open browser tab does not wake the GPU or keep it running. The UI polls every 15 seconds, and pauses while the tab is hidden. "Container up" means a container exists, but the API hasn't confirmed that vLLM is ready.

**Warm up** (`POST /serving-status/warmup`) is the only part that contacts vLLM: it polls `/health` until the server answers, exactly like `make gpu-warm`. Without `FALL_DETECTION_MODAL_APP`, the badge cannot see containers and only reflects warm-ups.

For a vLLM server started with `--api-key` (any host), set `FALL_DETECTION_INFERENCE_API_KEY` instead; the worker sends it as a bearer token.

### Reaching the local app from a phone

Browsers only allow camera access over HTTPS or on localhost. To use the locally running app from a phone, put both devices on a private [Tailscale](https://tailscale.com/) network and expose the web UI with `tailscale serve`, which provides HTTPS on a `*.ts.net` name visible only to your own devices. The Vite dev server listens on `127.0.0.1` and checks the `Host` header, so this needs a small dev-server configuration change that has not been made yet.
