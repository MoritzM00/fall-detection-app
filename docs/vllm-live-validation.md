# Live GPU/vLLM contract validation

For a partial real-serving check on Apple Silicon, see the [local Metal text smoke test](local-metal-validation.md). Metal currently excludes video input, so that test cannot replace this validation.

The [local upstream CPU experiment](local-cpu-validation.md) records a separate attempt with Qwen3-VL-2B-Instruct and Metal disabled.

Issue #9 requires a run against the actual GPU service. The local mock and unit tests establish the application-side request shape, but they cannot show which request fields a deployed vLLM/model stack accepts. The [vLLM documentation](https://docs.vllm.ai/en/latest/features/multimodal_inputs/#pre-extracted-frame-sequences-with-media_io_kwargs) describes the client-extracted `data:video/jpeg;base64,...` format and `media_io_kwargs.video` metadata. The validator exercises that contract through the real worker and persistence path.

From a machine that can reach the GPU service, with the same Python environment as this repository, run:

```sh
.venv/bin/python scripts/validate_vllm_contract.py \
  --base-url http://127.0.0.1:8000/v1 \
  --model EXACT_SERVED_MODEL_ID \
  --processor-version 'PROCESSOR_CLASS; transformers VERSION'
```

Use an SSH tunnel or an appropriately secured local connection for the example URL. For a protected endpoint such as the [Modal deployment](../deploy/README.md#modal-gpu-endpoint), export the same credential variables the worker uses (`FALL_DETECTION_INFERENCE_PROXY_KEY`/`_SECRET` or `FALL_DETECTION_INFERENCE_API_KEY`); the validator sends them on every request, including the `/version` and `/v1/models` checks. Supply the processor class and Transformers package version observed on the **GPU host**, rather than a local guess. The script queries `/version` for the server's vLLM version and `/v1/models` for the served model IDs. It refuses a model ID not returned by the server. It creates only a small generated moving-pattern clip in a temporary directory; no private video, credentials, or model weights are needed or written to the repository.

The validator prepares 16 saved JPEG frames, submits a real `vllm` job, and checks that the outgoing JSON contains exactly those encoded JPEG bytes and the saved FPS, frame indices, frame count, duration, and `do_sample_frames: false`. A successful response must parse into one of the 16 activity labels; the saved actual timestamps, model, backend kind, prepared bundle hash, and configuration must match the request. It then submits a second job to a deliberately unavailable local endpoint and checks that it becomes an explicit failed job with no prediction or mock fallback. The printed JSON is a summary, not the video payload or credentials. Record the actual version/model output and any server error in this document when the live run is performed.

The local self-test in `tests/test_vllm_contract_harness.py` checks validator plumbing with a fake HTTP response. **It is not evidence of GPU compatibility.** At the time this document was added, no target GPU endpoint or GPU-host processor version was configured in this workspace, and no live request had been made. Request acceptance and a valid class response would validate the transport and persistence contract, not fall detection accuracy.

## Live run: Modal L4, 2026-10-12

The validator passed against the [Modal deployment](../deploy/README.md#modal-gpu-endpoint) (`deploy/modal_vllm.py` at the commit that added this section).

| Item | Observed |
| --- | --- |
| Hardware | 1× NVIDIA L4 (24 GB), Modal |
| vLLM | `0.30.0` (from `/version`) |
| Model | `Qwen/Qwen3-VL-8B-Instruct`, served as `qwen3-vl-8b-instruct` (from `/v1/models`) |
| Processor | `Qwen3VLProcessor; transformers 5.19.0`, taken from the image build log rather than queried on the running host |
| Serving settings | `--max-model-len 5120`, `--max-num-seqs 4`, `--gpu-memory-utilization 0.92`, `--limit-mm-per-prompt '{"image": 0, "video": 1}'`, `VLLM_USE_FLASHINFER_SAMPLER=0` |
| Request | 16 prepared JPEG frames; the exact saved JPEG bytes and the FPS, frame indices, frame count, duration and `do_sample_frames: false` were confirmed in the outgoing JSON |
| Response | `The best answer is: other`, parsed to `other`; 16 sampled timestamps and the configuration were persisted |
| Failure path | A job against an unavailable endpoint became an explicit `failed` job (`inference request failed: timed out`), with no prediction and no mock fallback |

The input was the generated moving-pattern clip, so the `other` label says nothing about detection accuracy. This validates the transport and persistence contract only; research parity and capacity remain open in #41.

Compatibility findings from getting there:

- **Context length.** With `--max-model-len 16384`, engine start failed on the L4: 2.25 GiB of KV cache needed, 1.75 GiB available after the 16.65 GiB of weights. 5,120 tokens covers 16 frames even at the 672 px maximum crop (about 3.5k visual tokens).
- **FlashInfer sampler.** vLLM's FlashInfer top-k/top-p sampler JIT-compiles CUDA kernels during warmup and failed with `Could not find nvcc` on a slim image without the CUDA toolkit. `VLLM_USE_FLASHINFER_SAMPLER=0` selects the PyTorch sampler instead.
- **Modal redirects.** Modal answers requests running past about 150 s with `303 See Other`. The client and validators now follow redirects, so a request made during a cold start waits for the result instead of failing.

Cold start with weights already cached in the volume: weights loaded in 18 s, `torch.compile` took 74 s (its result is now cached in the vLLM volume) and engine initialization took 139 s in total, about 3.5 minutes from deploy to the server being ready. The warmed request took 1.2 s.
