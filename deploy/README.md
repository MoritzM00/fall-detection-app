# Deployment stub

No executable deployment configuration is included.

Planned application services: web, API, worker, PostgreSQL, and shared media volume. GPU serving remains on the existing separate server.

Default local development adds the mock inference HTTP service and points the worker to it. No GPU connection or weights are required. A real-inference deployment selects the GPU endpoint instead; both use the same client contract. In containers, address the mock by its service hostname rather than worker loopback.

Initial connection plan: GPU-local vLLM listener on port 8000; SSH forwarding from worker host port 8001; worker API base at `http://127.0.0.1:8001/v1`. These are proposed defaults, not an active endpoint. If the worker runs in a container, its loopback is separate from the host: tunnel placement/networking must be configured accordingly.

vLLM will serve `Qwen/Qwen3-VL-8B-Instruct`, optionally under an application alias. Verify hardware/version-specific startup settings before writing the launch configuration. Keep credentials and model/adapter weights outside Git.

Later: process supervision, private networking or HTTPS, secrets injection, health checks, backup/restore, and retention jobs. Server memory/parallelism/loading options are not ordinary browser-editable inference settings.
