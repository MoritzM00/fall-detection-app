# Deployment

The only executable deployment configuration is the Modal GPU endpoint described below. The application services themselves still run locally.

Planned application services: web, API, worker, PostgreSQL, and shared media volume. GPU serving remains on the existing separate server.

Default local development adds the mock inference HTTP service and points the worker to it. No GPU connection or weights are required. A real-inference deployment selects the GPU endpoint instead; both use the same client contract. In containers, address the mock by its service hostname rather than worker loopback.

Initial connection plan: GPU-local vLLM listener on port 8000; SSH forwarding from worker host port 8001; worker API base at `http://127.0.0.1:8001/v1`. These are proposed defaults, not an active endpoint. If the worker runs in a container, its loopback is separate from the host: tunnel placement/networking must be configured accordingly.

vLLM will serve `Qwen/Qwen3-VL-8B-Instruct`, optionally under an application alias. Verify hardware/version-specific startup settings before writing the launch configuration. Keep credentials and model/adapter weights outside Git.

Later: process supervision, private networking or HTTPS, secrets injection, health checks, backup/restore, and retention jobs. Server memory/parallelism/loading options are not ordinary browser-editable inference settings.

## Modal GPU endpoint (proposed, not yet validated)

[`modal_vllm.py`](modal_vllm.py) serves `Qwen/Qwen3-VL-8B-Instruct` with vLLM on a rented Modal GPU. The application keeps running locally (`make dev`), and only the worker talks to the endpoint. **No live request has been made against it yet**, so the vLLM version, GPU size and context length are untested starting points (see [vLLM live validation](../docs/vllm-live-validation.md)).

Cost and access controls:

- **Scale to zero.** The GPU container stops 10 minutes after the last request and is billed per second while it runs. `max_containers=1` means at most one GPU runs at a time. Set a workspace spending limit in the Modal dashboard as a hard cap.
- **Proxy auth.** Modal rejects requests without a valid proxy auth token at its edge, before any container starts, so a request without credentials never starts the GPU.
- **Cold start.** The first request after idling waits for the container to start and the model to load, which can take a few minutes; the first deploy also downloads about 17 GB of weights into a Modal volume. Raise `FALL_DETECTION_REQUEST_TIMEOUT_SECONDS` so the first window does not time out.

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
make dev
```

Keep the token in your shell or a Git-ignored env file, never in the repository. To take the endpoint down entirely, run `uvx modal app stop fall-detection-vllm`.

For a vLLM server started with `--api-key` (any host), set `FALL_DETECTION_INFERENCE_API_KEY` instead; the worker sends it as a bearer token.

### Reaching the local app from a phone

Browsers only allow camera access over HTTPS or on localhost. To use the locally running app from a phone, put both devices on a private [Tailscale](https://tailscale.com/) network and expose the web UI with `tailscale serve`, which provides HTTPS on a `*.ts.net` name visible only to your own devices. The Vite dev server listens on `127.0.0.1` and checks the `Host` header, so this needs a small dev-server configuration change that has not been made yet.
