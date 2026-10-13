# Deployment

The only executable deployment configuration is the Modal GPU endpoint described below. The application services themselves still run locally.

Planned application services: web, API, worker, PostgreSQL, and shared media volume. GPU serving remains on the existing separate server.

Default local development adds the mock inference HTTP service and points the worker to it. No GPU connection or weights are required. A real-inference deployment selects the GPU endpoint instead; both use the same client contract. In containers, address the mock by its service hostname rather than worker loopback.

Initial connection plan: GPU-local vLLM listener on port 8000; SSH forwarding from worker host port 8001; worker API base at `http://127.0.0.1:8001/v1`. These are proposed defaults, not an active endpoint. If the worker runs in a container, its loopback is separate from the host: tunnel placement/networking must be configured accordingly.

vLLM will serve `Qwen/Qwen3-VL-8B-Instruct`, optionally under an application alias. Verify hardware/version-specific startup settings before writing the launch configuration. Keep credentials and model/adapter weights outside Git.

Later: process supervision, private networking or HTTPS, secrets injection, health checks, backup/restore, and retention jobs. Server memory/parallelism/loading options are not ordinary browser-editable inference settings.

## Modal GPU endpoint

[`modal_vllm.py`](modal_vllm.py) serves `Qwen/Qwen3-VL-8B-Instruct` (function `serve`, the default model) and `Qwen/Qwen3-VL-8B-Thinking` (function `serve_thinking`) with vLLM on rented Modal GPUs. Each model runs in its own function with its own URL and L4, since one vLLM process serves one model and an L4 holds one 8B model. The app lists both in the model picker and sends each run to its model's endpoint; `FALL_DETECTION_ADDITIONAL_MODELS` in `modal.env` names the Thinking endpoint. The application keeps running locally (`make dev`), and only the worker talks to the endpoint. The request contract was [validated live on an L4](../docs/vllm-live-validation.md#live-run-modal-l4-2026-10-12) on 2026-10-12; capacity and research parity have not been measured yet.

Cost and access controls:

- **Scale to zero.** Each GPU container stops 10 minutes after the last request to its model and is billed per second while it runs. `max_containers=1` per function means at most one GPU per model, so two while both models are in use. Set a workspace spending limit in the Modal dashboard as a hard cap.
- **Proxy auth.** Modal rejects requests without a valid proxy auth token at its edge, before any container starts, so a request without credentials never starts the GPU.
- **Cold start.** The first request after idling waits for the container to start and the model to load: about 3.5 minutes in the first measured start, including a 74 s `torch.compile` that is now cached. The very first start also downloads about 17 GB of weights into a Modal volume. A later cold start from the web UI's **Warm up** button took 172 s. Warm up when you start a session (button or `make gpu-warm`) so the GPU is ready by the time you submit, and raise `FALL_DETECTION_REQUEST_TIMEOUT_SECONDS` so a window sent during a cold start does not time out.

Setup:

```sh
uvx modal setup                              # once: authenticate the Modal CLI
uvx modal deploy deploy/modal_vllm.py        # prints the endpoint URL
```

Configuration is split by sensitivity:

| File | Committed | Contents |
| --- | --- | --- |
| [`modal.env`](modal.env) | yes | Backend kind, endpoint URL, Modal app name, context window, request timeout |
| [`.env.example`](../.env.example) | yes | Names of the required secrets, with placeholders |
| `.env` | no (Git-ignored) | Your Modal proxy auth token |

The endpoint URL is public in this repository. That is harmless: without the token, Modal rejects requests at its edge before any container starts. Create a proxy auth token in the Modal dashboard (Settings → Proxy Auth Tokens), put it in `.env`, and start the app against the endpoint:

```sh
cp .env.example .env    # then fill in the token
make dev-gpu            # web UI, API and worker against the Modal GPU
make gpu-warm           # optional: wake the GPU and print how long it took
make gpu-warm MODEL=qwen3-vl-8b-thinking   # the same for the Thinking model
make gpu-stop           # optional: stop the GPU now instead of after 10 idle minutes
```

Both targets load `modal.env` first and `.env` second, so a value in `.env` overrides the committed one, for example to point at a different deployment. Plain `make dev` keeps using the mock. If you redeploy under a different app or workspace name, update `modal.env`. To take the endpoint down entirely, run `uvx modal app stop fall-detection-vllm`.

### Where video frames go

- **In transit: encrypted.** The worker sends frames to the Modal endpoint over HTTPS, with certificate verification. It refuses to send frames over plain HTTP unless the endpoint is on this machine (the local mock or an SSH tunnel to `127.0.0.1`/`localhost`); such a job fails explicitly instead.
- **At Modal: readable by the provider.** Modal terminates TLS at its edge and forwards the request to the container, where vLLM decodes the frames in memory. This is not end-to-end encryption: as with any cloud GPU, the provider's infrastructure can technically access the frames while they are processed.
- **Storage:** the endpoint does not store requests, and vLLM is started without `--enable-log-requests`, so request contents are not logged. A spot check of recent Modal logs found only request lines, no frame data.
- **Region:** Modal chooses where the container runs unless the function pins a `region`. Check plan availability and pricing before pinning, for example to keep footage of identifiable people in the EU.

In vLLM mode the web UI shows this as a notice naming the endpoint host. Only use footage you are allowed to share with the hosting provider.

### GPU status in the web UI

With the vLLM backend, the top bar shows the GPU state instead of a static label: idle (scaled to zero), container up, warming up, ready, stopping, or unknown. The API reads it from Modal's control plane (`Function.get_current_stats`) using your local Modal CLI login, caches it for 5 seconds, and **never sends a request to vLLM**, so an open browser tab does not wake the GPU or keep it running. The UI polls every 15 seconds (every 3 seconds while warming up or stopping), and pauses while the tab is hidden. "Container up" means a container exists, but the API hasn't confirmed that vLLM is ready.

With more than one model, the top bar shows one badge per model, and `?model=<name>` selects the model on every serving-status route (the default model when omitted).

**Warm up** (`POST /serving-status/warmup`) is the only part that contacts vLLM: it polls `/health` until the server answers, exactly like `make gpu-warm`. Without `FALL_DETECTION_MODAL_APP`, the badge cannot see containers and only reflects warm-ups.

**Shut down** (`POST /serving-status/shutdown`, or `make gpu-stop`) stops the app's GPU containers right away instead of waiting for the 10-minute idle scale-down. The app stays deployed, so the next request or warm-up cold-starts a new container. The Modal SDK has no public call for this, so the API runs `modal container list` and `modal container stop` with your local Modal login. Shutdown stops every model's containers, because they share the Modal app. It is refused (HTTP 409) while requests are running or queued for any model: Modal reschedules a cancelled request on a new container, which would start the GPU again. It is also refused during a warm-up or another shutdown, and a warm-up of any model is refused (HTTP 409) while a shutdown runs. While the stop runs, every badge greys out its buttons. Afterwards the badge shows "stopping" for as long as Modal still counts a container, at most 120 seconds, and only until new requests arrive. A running monitoring session will wake the GPU again with its next window.

For a vLLM server started with `--api-key` (any host), set `FALL_DETECTION_INFERENCE_API_KEY` instead; the worker sends it as a bearer token.

### Reaching the local app from a phone

Browsers only allow camera access over HTTPS or on localhost. To use the locally running app from a phone, put both devices on a private [Tailscale](https://tailscale.com/) network and expose the web UI with `tailscale serve`, which provides HTTPS on a `*.ts.net` name visible only to your own devices. The Vite dev server listens on `127.0.0.1` and checks the `Host` header, so this needs a small dev-server configuration change that has not been made yet.

The API itself only answers to the host names `127.0.0.1` and `localhost` (any port) and returns HTTP 400 for any other `Host` header, which blocks DNS-rebinding pages from reading its responses. The Vite proxy rewrites `Host` to the API's own `127.0.0.1` address (`changeOrigin`), so the phone setup above needs no API change as long as the phone reaches the API through the web UI. Only if a client reaches the API directly under another name, set `FALL_DETECTION_ALLOWED_HOSTS` to a comma-separated list of the extra names, for example `FALL_DETECTION_ALLOWED_HOSTS=laptop.tail1234.ts.net`. Loopback names stay allowed.
