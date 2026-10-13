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
| Serving settings | `--max-model-len 5120` (8192 since 2026-10-13, see below), `--max-num-seqs 4`, `--gpu-memory-utilization 0.92`, `--limit-mm-per-prompt '{"image": 0, "video": 1}'`, `VLLM_USE_FLASHINFER_SAMPLER=0` |
| Request | 16 prepared JPEG frames; the exact saved JPEG bytes and the FPS, frame indices, frame count, duration and `do_sample_frames: false` were confirmed in the outgoing JSON |
| Response | `The best answer is: other`, parsed to `other`; 16 sampled timestamps and the configuration were persisted |
| Failure path | A job against an unavailable endpoint became an explicit `failed` job (`inference request failed: timed out`), with no prediction and no mock fallback |

The input was the generated moving-pattern clip, so the `other` label says nothing about detection accuracy. This validates the transport and persistence contract only; research parity and capacity remain open in #41.

Compatibility findings from getting there:

- **Context length.** With `--max-model-len 16384`, engine start failed on the L4: 2.25 GiB of KV cache needed, 1.75 GiB available after the 16.65 GiB of weights. 5,120 tokens covers 16 frames even at the 672 px maximum crop (about 3.5k visual tokens). The context is now 8,192 tokens so the chain-of-thought preset's 4,096 output tokens fit; the engine started with it on the L4 (see the chain-of-thought run below).
- **FlashInfer sampler.** vLLM's FlashInfer top-k/top-p sampler JIT-compiles CUDA kernels during warmup and failed with `Could not find nvcc` on a slim image without the CUDA toolkit. `VLLM_USE_FLASHINFER_SAMPLER=0` selects the PyTorch sampler instead.
- **Modal redirects.** Modal answers requests running past about 150 s with `303 See Other`. The client and validators now follow redirects, so a request made during a cold start waits for the result instead of failing. The inference client, warm-up and validators follow only redirects to the same scheme, host and port as the configured endpoint; a redirect anywhere else fails explicitly and sends nothing to the new origin.

- **Request limits (issue #77).** The app accepts up to 32 frames, 672 px and 4096 max tokens, which can exceed 5,120 tokens. `deploy/modal.env` now sets `FALL_DETECTION_MAX_MODEL_LEN=5120`, and the API rejects such configurations with 422 at submission using a conservative estimate (see [contracts](contracts.md)). At the time this was measured, with the earlier thesis prompt and 32 max tokens, 16 frames fit at every offered crop and 32 frames fit up to 512 px. With the current zero-shot preset (its longer prompt and 64 max tokens), 16 frames fit at every offered crop, 32 frames fit up to 480 px, and 16 × 448 leaves room for up to 2,660 max tokens. The chain-of-thought preset's 4,096 max tokens did not fit 5,120 (6,446 tokens estimated at 16 × 448), so the context was raised to 8,192. With 8,192, both presets fit 16 frames up to 640 px (16 × 672 leaves room for up to 3,772 max tokens), and the zero-shot preset fits 32 frames at every crop. **Not yet checked live:** the largest configuration the server actually accepts and its rejection message for an oversized request still need to be recorded here from a run against the endpoint; this check is tracked in #41.

Cold start with weights already cached in the volume: weights loaded in 18 s, `torch.compile` took 74 s (its result is now cached in the vLLM volume) and engine initialization took 139 s in total, about 3.5 minutes from deploy to the server being ready. The warmed request took 1.2 s.

## Chain-of-thought run, 2026-10-13

Same L4 deployment and vLLM 0.30.0 with `--max-model-len 8192`; cold start to healthy took 236 s. An API and worker from branch `feat/cot-answer-parsing`, with an isolated data directory, sent GMDCSA24 clips (uploaded copies, 16 frames at 7.5 FPS, 448 px) with the video before the prompt text.

| Clip and window | Annotation | Preset | Label | Tokens (prompt / completion) | Request |
| --- | --- | --- | --- | --- | --- |
| `Subject_1/Fall/01.mp4`, 3.4–5.4 s | falling 3.4–6 s | `research-zeroshot-v2` | `lie_down` | 1,820 / 8 | 2.7 s |
| same | same | `research-zeroshot-cot-v2` | `lie_down` | 1,791 / 194 | 13.1 s |
| `Subject_1/ADL/01.mp4`, 4.0–6.0 s | sleeping 2–8 s | `research-zeroshot-v2` | `lying` | 1,820 / 7 | 2.3 s |
| same | same | `research-zeroshot-cot-v2` | `lying` | 1,791 / 234 | 15.5 s |

Every response parsed. The chain-of-thought answers reasoned in numbered steps with no think tags and ended with `The best answer is: <label>`; the text before it was saved as the reasoning. One reasoning listed all 16 labels, so reading the label from the whole response, not the answer, would have been ambiguous. A chain-of-thought request at 16 × 672 was rejected with 422 before reaching the GPU (estimate 8,406 tokens). Two clips are not accuracy evidence: the fall the model read as a deliberate `lie_down` is the kind of case research parity work in #41 has to measure.



## Qwen3-VL-8B-Thinking run, 2026-10-13

`Qwen/Qwen3-VL-8B-Thinking` served as `qwen3-vl-8b-thinking` from its own Modal function (`serve_thinking`) on a second L4, with the same vLLM settings and no reasoning parser. Its first start, including the weight download, took 464 s; after it scaled down, the next request waited 162 s for a cold start. The app routed runs with this model to its endpoint and runs with the default model to `serve`, and `make gpu-stop` stopped both. Same clips and frames as the chain-of-thought run above.

| Clip and window | Settings | Label | Completion tokens | Request |
| --- | --- | --- | --- | --- |
| `Subject_1/Fall/01.mp4`, 3.4–5.4 s | `research-zeroshot-cot-v2` | `fall` | 197 | 162.0 s (cold start) |
| same | `research-zeroshot-v2` (64 max tokens) | failed: answer truncated | — | — |
| same | zero-shot prompt, CoT sampling (4,096 max tokens) | `fall` | 164 | 11.2 s |
| `Subject_1/ADL/01.mp4`, 4.0–6.0 s | `research-zeroshot-cot-v2` | `lying` | 144 | 11.0 s |
| same | `research-zeroshot-v2` (64 max tokens) | failed: answer truncated | — | — |
| same | zero-shot prompt, CoT sampling (4,096 max tokens) | `lying` | 124 | 8.8 s |

Every completed response had the form `<reasoning>\n</think>\n\nThe best answer is: <label>`: the chat template opens the think block, so only the closing tag appears, and the parser set the reasoning aside with either strategy. With the zero-shot preset's 64 max tokens, the model was cut off mid-reasoning and the run failed explicitly, which is why the UI switches a Thinking selection to chain of thought and warns below 1,024 max tokens. On the fall window the Thinking model answered `fall` where the Instruct model answered `lie_down`. Two clips are not accuracy evidence.
