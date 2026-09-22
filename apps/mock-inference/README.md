# Mock inference service

The implementation now lives in [`../mock_inference`](../mock_inference) so it can be imported as a Python package. This directory remains only as a pointer for older planning links.

Expose the subset of the vLLM-compatible API used by the worker: model discovery and non-streaming chat completions. Return the expected response envelope containing thesis-style generated text. Support deterministic scenario fixtures, simulated latency, and failures.

This service needs neither GPU libraries nor model weights. It must accept the same request shape as real serving; do not replace the worker pipeline with canned application results.

See [mock contract and scenarios](../../docs/mock-inference.md).
