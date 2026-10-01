"""Opt-in real text-serving smoke test; does not validate the video contract."""

import argparse
import json
import math
import socket
from typing import Any

from fall_detection.inference import InferenceClient, InferenceServiceError
from fall_detection.parsing import PredictionParseError, parse_activity_label
from fall_detection.taxonomy import ACTIVITY_LABELS
from scripts.validate_vllm_contract import _server_identity


def validate(base_url: str, model: str, timeout: float = 120) -> dict[str, Any]:
    """Exercise real completions and explicit failures through the application client."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    base_url = base_url.rstrip("/")
    version, served_models = _server_identity(base_url, model)
    client = InferenceClient(base_url, timeout_seconds=timeout)

    def payload(text: str, *, max_tokens: int = 32) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }

    def expect_service_error(request: dict[str, Any], fragment: str) -> None:
        try:
            client.complete(request)
        except InferenceServiceError as exc:
            if fragment not in str(exc):
                raise RuntimeError(f"unexpected service error: {exc}") from exc
        else:
            raise RuntimeError("expected request to fail explicitly")

    answers = {}
    for label in ACTIVITY_LABELS:
        response = client.complete(
            payload(f"Reply with exactly this text and nothing else: The best answer is: {label}")
        )
        try:
            parsed = parse_activity_label(response.content)
        except PredictionParseError as exc:
            raise RuntimeError(f"unexpected answer for {label}: {response.content!r}") from exc
        if not response.completion_id or parsed != label:
            raise RuntimeError(f"unexpected answer for {label}: {response.content!r}")
        answers[label] = response.content

    bare = client.complete(payload("Reply with exactly this word and nothing else: walk"))
    if parse_activity_label(bare.content, allow_bare_label=True) != "walk":
        raise RuntimeError(f"unexpected bare answer: {bare.content!r}")
    invalid = client.complete(payload("Reply with exactly this word and nothing else: banana"))
    try:
        parse_activity_label(invalid.content, allow_bare_label=True)
    except PredictionParseError:
        pass
    else:
        raise RuntimeError("expected non-activity output to fail parsing")

    expect_service_error(
        payload("Write a paragraph of at least fifty words about walking.", max_tokens=1),
        "truncated",
    )
    missing_model = model + "-intentionally-unserved"
    if missing_model in served_models:
        raise RuntimeError("negative-test model unexpectedly exists")
    expect_service_error({**payload("Hello"), "model": missing_model}, "404")

    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        try:
            InferenceClient(f"http://127.0.0.1:{port}/v1", timeout_seconds=1).complete(
                payload("Hello")
            )
        except InferenceServiceError:
            pass
        else:
            raise RuntimeError("expected unavailable endpoint to fail")

    return {
        "scope": "text HTTP client and parser only; no video, worker, or persistence validation",
        "vllm_version": version,
        "served_model_ids": served_models,
        "requested_model": model,
        "temperature": 0,
        "max_tokens": 32,
        "enable_thinking": False,
        "answers": answers,
        "bare_label_accepted": True,
        "invalid_label_rejected": True,
        "truncation_rejected": True,
        "unserved_model_rejected": True,
        "unavailable_endpoint_rejected": True,
        "video_contract_validated": False,
    }


def main() -> None:
    """Run explicitly against an existing server; install or download nothing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001/v1")
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    print(json.dumps(validate(args.base_url, args.model, args.timeout), indent=2))


if __name__ == "__main__":
    main()
