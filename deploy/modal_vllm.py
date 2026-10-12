"""Serve Qwen3-VL-8B-Instruct with vLLM on Modal as a private, scale-to-zero endpoint.

Deploy with ``uvx modal deploy deploy/modal_vllm.py``. Requests must carry a Modal proxy
auth token (``Modal-Key`` / ``Modal-Secret`` headers); Modal rejects anything else at its
edge, before a GPU container is started, so unauthenticated traffic cannot run up cost.
"""

import os
import subprocess
import threading

import modal

# vLLM must support request-level video ``media_io_kwargs`` and Qwen3-VL.
VLLM_VERSION = "0.30.0"
MODEL_ID = "Qwen/Qwen3-VL-8B-Instruct"
# Matches the worker default for FALL_DETECTION_INFERENCE_MODEL.
SERVED_MODEL_NAME = "qwen3-vl-8b-instruct"
# 24 GB fits bf16 weights plus a ~12k-token KV cache; 16 frames at the 672 px maximum
# crop need about 3.5k visual tokens. Switch to "L40S" (48 GB) for longer contexts.
GPU = "L4"
PORT = 8000
MINUTES = 60

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(f"vllm=={VLLM_VERSION}")
    # The FlashInfer sampler JIT-compiles CUDA kernels, but the slim image has no nvcc.
    .env({"VLLM_USE_FLASHINFER_SAMPLER": "0"})
)

# Weights (~17 GB) and compilation artifacts persist across cold starts.
hf_cache = modal.Volume.from_name("fall-detection-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("fall-detection-vllm-cache", create_if_missing=True)

app = modal.App("fall-detection-vllm")


@app.function(
    image=image,
    gpu=GPU,
    volumes={"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache},
    # Stop paying ten minutes after the last request; never run more than one GPU.
    scaledown_window=10 * MINUTES,
    max_containers=1,
    timeout=60 * MINUTES,
)
@modal.concurrent(max_inputs=8)
@modal.web_server(port=PORT, startup_timeout=20 * MINUTES, requires_proxy_auth=True)
def serve() -> None:
    """Start the OpenAI-compatible vLLM server inside the GPU container."""
    server = subprocess.Popen(
        [
            "vllm",
            "serve",
            MODEL_ID,
            "--served-model-name",
            SERVED_MODEL_NAME,
            "--host",
            "0.0.0.0",
            "--port",
            str(PORT),
            "--max-model-len",
            "5120",
            "--max-num-seqs",
            "4",
            "--gpu-memory-utilization",
            "0.92",
            "--limit-mm-per-prompt",
            '{"image": 0, "video": 1}',
        ]
    )

    # A crashed server would otherwise leave an idle GPU container running until timeout.
    def exit_with_server() -> None:
        os._exit(server.wait() or 1)

    threading.Thread(target=exit_with_server, daemon=True).start()
