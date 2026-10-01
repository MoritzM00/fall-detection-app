# Local upstream vLLM CPU video experiment

This experiment tests the existing application video contract on Apple's CPU, independently of vLLM Metal and research behavior. It uses `Qwen/Qwen3-VL-2B-Instruct`, generated media only, and the upstream vLLM 0.30.0+cpu macOS arm64 wheel already installed in the isolated [Metal smoke-test environment](local-metal-validation.md). `VLLM_PLUGINS=''` disables the installed Metal plugin. A platform probe returned `CpuPlatform` and successfully imported `vllm._C`.

Host: Apple M3 Pro, 18 GiB unified memory, macOS 15.7.3. Model revision resolved during the experiment: `89644892e4d85e24eaac8bacfd4f463576704203`. Environment: Python 3.12.12, vLLM 0.30.0+cpu, Torch 2.13.0, Transformers 5.18.0.

## Server command

```sh
VLLM_PLUGINS='' HF_HOME=/private/tmp/fall-cpu-hf-cache \
  VLLM_CPU_KVCACHE_SPACE=1 VLLM_CPU_OMP_THREADS_BIND=nobind \
  OMP_NUM_THREADS=4 VLLM_NO_USAGE_STATS=1 \
  /private/tmp/fall-vllm-metal-venv/bin/vllm serve Qwen/Qwen3-VL-2B-Instruct \
  --host 127.0.0.1 --port 8002 --dtype float16 \
  --max-model-len 4096 --max-num-seqs 1 --enforce-eager \
  --limit-mm-per-prompt '{"image":0,"video":1}' \
  --mm-processor-kwargs '{"size":{"longest_edge":50176,"shortest_edge":50176}}'
```

The small vision pixel budget, single sequence, and 1 GiB KV cache bound this functional test. They are experiment settings, not research defaults. For fixed weights on later runs, add `--revision 89644892e4d85e24eaac8bacfd4f463576704203`. The environment and model cache remain outside the repository.

The first launch used `VLLM_CPU_OMP_THREADS_BIND=none`, which this version rejects while parsing CPU IDs. Reading its thread-binding implementation identified `nobind` as the supported setting; the corrected launch progressed to loading the CPU model with Torch SDPA for vision attention.

## Observed results, 2026-10-01

The corrected server started successfully using `device_config=cpu`, `CPUWorker`, and Torch SDPA vision attention. The weights downloaded to the temporary cache (about 4 GB); no source build or Metal execution was needed for this wheel.

The unchanged baseline validator sent 16 prepared 224×224 JPEG frames as `video_url` with request-level `media_io_kwargs.video`. The server accepted the requests (HTTP 200), but two baseline jobs failed strict response parsing. The diagnostic run returned `The best answer is: fall<0.1 seconds>` in 1.274 seconds. The first run's raw response was not captured. These are generated moving-pattern frames with no person; the diagnostic response is not accuracy evidence, and its timestamp suffix violates the application format.

A separate experiment appended the explicit formatting instruction below to the baseline prompt and persisted it with preset `custom`. The application parser and video transport were unchanged. It returned `The best answer is: other` in 3.484 seconds and passed the full contract validator: exact saved JPEG bytes and video metadata, all 16 actual frame timestamps, model/backend identity, prepared bundle hash and configuration persistence, and an unavailable-endpoint failed job with no prediction or mock fallback. Here `other` was a parsed model answer, not a processing failure. A second run using the repeatable CLI option also passed.

This establishes local CPU compatibility for the exercised video request and custom prompt, not general format-following reliability, CUDA/GPU compatibility, throughput, activity accuracy, or research parity. Timings are individual requests with caching enabled, not benchmarks. No private clips were used. Test servers were stopped after validation.

## Repeat the successful custom-prompt test

From the application repository, create a temporary prompt file:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
from fall_detection.prompts import THESIS_BASELINE_PROMPT

extra = (
    "\n\nThe entire response must be exactly one line: The best answer is: "
    "followed by one class label. Do not include timestamps, time ranges, "
    "angle brackets, punctuation after the label, or explanations."
)
Path("/private/tmp/fall-cpu-prompt.txt").write_text(
    THESIS_BASELINE_PROMPT + extra, encoding="utf-8"
)
PY

.venv/bin/python -m scripts.validate_vllm_contract \
  --base-url http://127.0.0.1:8002/v1 \
  --model Qwen/Qwen3-VL-2B-Instruct \
  --processor-version 'Qwen3VLProcessor; transformers 5.18.0' \
  --timeout 180 --prompt-file /private/tmp/fall-cpu-prompt.txt
```

Omit `--prompt-file` to exercise the original baseline. The optional file is read as UTF-8, sent verbatim, and saved in both test jobs. The report includes the prompt preset and raw model response. The default baseline and production worker are unchanged; the experiment requires an explicit custom prompt.
