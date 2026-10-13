"""Serve Qwen3-VL-8B Instruct and Thinking with vLLM on Modal as private, scale-to-zero endpoints.

Each model runs in its own function on its own GPU, so each has its own URL: ``serve``
(Instruct) and ``serve_thinking`` (Thinking).

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
# Reasons in a think block before every answer. No --reasoning-parser is set, so the
# response keeps the reasoning and the app saves the whole text and splits it itself.
THINKING_MODEL_ID = "Qwen/Qwen3-VL-8B-Thinking"
# Matches FALL_DETECTION_ADDITIONAL_MODELS in deploy/modal.env.
THINKING_SERVED_MODEL_NAME = "qwen3-vl-8b-thinking"
# 24 GB fits bf16 weights plus a ~12k-token KV cache; 16 frames at the 672 px maximum
# crop need about 3.5k visual tokens, and the chain-of-thought preset allows 4,096 output
# tokens, hence an 8,192-token context. Switch to "L40S" (48 GB) for longer contexts.
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


GPU_FUNCTION = {
    "image": image,
    "gpu": GPU,
    "volumes": {"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache},
    # Stop paying ten minutes after the last request; never run more than one GPU per model.
    "scaledown_window": 10 * MINUTES,
    "max_containers": 1,
    "timeout": 60 * MINUTES,
}


def start_vllm(model_id: str, served_model_name: str) -> None:
    """Start the OpenAI-compatible vLLM server inside the GPU container."""
    server = subprocess.Popen(
        [
            "vllm",
            "serve",
            model_id,
            "--served-model-name",
            served_model_name,
            "--host",
            "0.0.0.0",
            "--port",
            str(PORT),
            # Keep FALL_DETECTION_MAX_MODEL_LEN in deploy/modal.env equal to this value.
            "--max-model-len",
            "8192",
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


@app.function(**GPU_FUNCTION)
@modal.concurrent(max_inputs=8)
@modal.web_server(port=PORT, startup_timeout=20 * MINUTES, requires_proxy_auth=True)
def serve() -> None:
    """Serve Qwen3-VL-8B-Instruct, the default model."""
    start_vllm(MODEL_ID, SERVED_MODEL_NAME)


@app.function(**GPU_FUNCTION)
@modal.concurrent(max_inputs=8)
@modal.web_server(port=PORT, startup_timeout=20 * MINUTES, requires_proxy_auth=True)
def serve_thinking() -> None:
    """Serve Qwen3-VL-8B-Thinking, which reasons before every answer."""
    start_vllm(THINKING_MODEL_ID, THINKING_SERVED_MODEL_NAME)
