# Live GPU/vLLM contract validation

Issue #9 requires a run against the actual GPU service. The local mock and unit tests establish the application-side request shape, but they cannot show which request fields a deployed vLLM/model stack accepts. The [vLLM documentation](https://docs.vllm.ai/en/latest/features/multimodal_inputs/#pre-extracted-frame-sequences-with-media_io_kwargs) describes the client-extracted `data:video/jpeg;base64,...` format and `media_io_kwargs.video` metadata. The validator exercises that contract through the real worker and persistence path.

From a machine that can reach the GPU service, with the same Python environment as this repository, run:

```sh
.venv/bin/python scripts/validate_vllm_contract.py \
  --base-url http://127.0.0.1:8000/v1 \
  --model EXACT_SERVED_MODEL_ID \
  --processor-version 'PROCESSOR_CLASS; transformers VERSION'
```

Use an SSH tunnel or an appropriately secured local connection for the example URL. Supply the processor class and Transformers package version observed on the **GPU host**, rather than a local guess. The script queries `/version` for the server's vLLM version and `/v1/models` for the served model IDs. It refuses a model ID not returned by the server. It creates only a small generated moving-pattern clip in a temporary directory; no private video, credentials, or model weights are needed or written to the repository.

The validator prepares 16 saved JPEG frames, submits a real `vllm` job, and checks that the outgoing JSON contains exactly those encoded JPEG bytes and the saved FPS, frame indices, frame count, duration, and `do_sample_frames: false`. A successful response must parse into one of the 16 activity labels; the saved actual timestamps, model, backend kind, prepared bundle hash, and configuration must match the request. It then submits a second job to a deliberately unavailable local endpoint and checks that it becomes an explicit failed job with no prediction or mock fallback. The printed JSON is a summary, not the video payload or credentials. Record the actual version/model output and any server error in this document when the live run is performed.

The local self-test in `tests/test_vllm_contract_harness.py` checks validator plumbing with a fake HTTP response. **It is not evidence of GPU compatibility.** At the time this document was added, no target GPU endpoint or GPU-host processor version was configured in this workspace, and no live request had been made. Request acceptance and a valid class response would validate the transport and persistence contract, not fall detection accuracy.
