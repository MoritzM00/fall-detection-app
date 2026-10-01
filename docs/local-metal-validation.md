# Local vLLM Metal text smoke test

This is an application HTTP-client and parser experiment, separate from verified research behavior. It does not run the worker, persist predictions, validate timestamps/configuration snapshots, test video requests, or establish fall-detection accuracy. The current Metal [support matrix](https://github.com/vllm-project/vllm-metal/blob/main/docs/supported_models.md) excludes video input. Keep using the [video contract validator](vllm-live-validation.md) for that boundary.

The opt-in script discovers `/version` and `/v1/models`, then uses the production `InferenceClient` and activity parser. It asks a real text model to repeat each of the 16 canonical answers, checks custom bare-label parsing, and requires explicit rejection of invalid model output, truncated output, an unserved model, and an unavailable endpoint. These are format-following tests, not activity predictions. The script never converts failures to `other` and writes no application data.

## Reproduce

Requires Apple Silicon and macOS 15+. Keep the serving environment separate from the app environment. The official release installer is documented [here](https://github.com/vllm-project/vllm-metal/blob/main/docs/installation.md). The run in this workspace used pinned prebuilt wheels in `/private/tmp`:

```sh
UV_CACHE_DIR=/private/tmp/fall-metal-uv-cache uv venv --python 3.12 /private/tmp/fall-vllm-metal-venv
UV_CACHE_DIR=/private/tmp/fall-metal-uv-cache uv pip install \
  --python /private/tmp/fall-vllm-metal-venv/bin/python \
  'https://github.com/vllm-project/vllm/releases/download/v0.30.0/vllm-0.30.0%2Bcpu-cp312-cp312-macosx_11_0_arm64.whl' \
  'https://github.com/vllm-project/vllm-metal/releases/download/v0.30.0/vllm_metal-0.30.0-cp312-cp312-macosx_15_0_arm64.whl'

HF_HOME=/private/tmp/fall-metal-hf-cache VLLM_NO_USAGE_STATS=1 \
  /private/tmp/fall-vllm-metal-venv/bin/vllm serve Qwen/Qwen3-0.6B \
  --host 127.0.0.1 --port 8001 --max-model-len 2048 --max-num-seqs 1 \
  --gpu-memory-utilization 0.3
```

After the server reports ready, run in another terminal from the application repository:

```sh
.venv/bin/python -m scripts.validate_text_serving \
  --base-url http://127.0.0.1:8001/v1 --model Qwen/Qwen3-0.6B
```

The script sends temperature 0, max tokens 32, non-streaming text content parts, and Qwen's `chat_template_kwargs.enable_thinking=false`. The truncation probe intentionally uses one output token. The thinking setting is specific to this smoke test, not a change to the production video payload. A nonconforming answer fails the run rather than being repaired or retried silently. Stop the server with Ctrl-C when finished. Temporary environments and model caches may disappear on system cleanup; no weights or credentials belong in Git.

The offline tests in `tests/test_text_serving_harness.py` check that reporting cannot pass when a negative parsing probe returns an accepted class. They are not live-serving evidence.

## Observed local results, 2026-10-01

Host: Apple M3 Pro, 18 GiB unified memory, macOS 15.7.3, arm64. Serving environment: Python 3.12.12, vLLM wheel 0.30.0+cpu (HTTP `/version`: `0.30.0`), vllm-metal 0.30.0, MLX 0.32.1, mlx-lm 0.32.0, Transformers 5.18.0, Torch 2.13.0. The server logs confirmed `MLX device set to: Device(gpu, 0)` and `PyTorch device set to: mps`; the CPU wheel name does not mean this run used CPU model execution.

Served ID: `Qwen/Qwen3-0.6B`. Hugging Face revision resolved during the run: `c1899de289a04d12100db370d81485cdf75e47ca`. For the same weights on a later run, add `--revision c1899de289a04d12100db370d81485cdf75e47ca` to the launch command. The installed dependency versions above describe this run; future wheel installation may resolve newer dependencies.

The default Metal memory utilization of 0.92 produced a reported 9.91 GB KV-cache budget and the engine exited with signal 9 during initialization. The log does not establish why it was killed. Utilization 0.2 left no cache space and failed explicitly. Utilization 0.3 started successfully, reporting a 1.26 GB KV budget on the first successful startup and 1.92 GB on the second (available memory differed).

Both successful server startups had an initial completion rejected by the strict parser. The second startup's raw answer was captured as `The best answer is: walk.`; the trailing period violates the required format. The first startup's raw answer was not captured. A subsequent invocation on each server passed the entire smoke test: all 16 exact-format labels, bare `walk`, invalid-label rejection, truncation rejection, HTTP 404 for the unserved model, and connection-failure rejection. The reason for the initial/subsequent output difference remains unverified. No parser relaxation, automatic repair, or automatic retry was added to obtain these results.

This is evidence that real Metal-served completions can traverse the application client and parser, including negative cases. It is not an unconditional pass for small-model format following, cold-start consistency, the production video payload, timestamp/configuration persistence, or research parity. The test servers were stopped after validation; the temporary environment and cache remain available for reruns.

Repository verification: 354 Python tests passed; Ruff lint/format and ty checks passed. The app's serving dependency lockfile and production inference behavior were unchanged.
