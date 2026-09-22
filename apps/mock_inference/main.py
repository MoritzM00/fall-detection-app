import asyncio
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from fall_detection.taxonomy import ACTIVITY_LABELS

SERVED_MODEL = os.getenv("MOCK_INFERENCE_MODEL", "qwen3-vl-8b-instruct")
FIXED_LABEL = os.getenv("MOCK_INFERENCE_LABEL", "fall")
DELAY_MS = int(os.getenv("MOCK_INFERENCE_DELAY_MS", "900"))

app = FastAPI(title="Mock vLLM inference", version="0.1.0")


class ChatCompletionRequest(BaseModel):
    """Supported subset of the OpenAI-compatible completion request."""

    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    temperature: float = 0
    max_tokens: int = Field(default=32, gt=0)
    stream: bool = False


def validate_video_message(messages: list[dict[str, Any]]) -> None:
    """Validate the exact multimodal subset the application depends on."""
    if len(messages) != 1 or messages[0].get("role") != "user":
        raise HTTPException(status_code=400, detail="Exactly one user message is required")
    content = messages[0].get("content")
    if not isinstance(content, list):
        raise HTTPException(status_code=400, detail="Message content must be a list")
    content_types = {part.get("type") for part in content if isinstance(part, dict)}
    if content_types != {"text", "video_url"}:
        raise HTTPException(status_code=400, detail="Text and video_url content are required")
    video_parts = [part for part in content if part.get("type") == "video_url"]
    video_url = video_parts[0].get("video_url") if video_parts else None
    if not isinstance(video_url, dict) or not isinstance(video_url.get("url"), str):
        raise HTTPException(status_code=400, detail="video_url.url is required")


@app.get("/health")
def health() -> dict[str, str]:
    """Report mock process health and identity."""
    return {"status": "ok", "backend": "mock"}


@app.get("/v1/models")
def models() -> dict[str, object]:
    """List the single deterministic served-model alias."""
    return {
        "object": "list",
        "data": [
            {
                "id": SERVED_MODEL,
                "object": "model",
                "created": 0,
                "owned_by": "fall-detection-mock",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest) -> dict[str, object]:
    """Return one delayed thesis-format response after contract validation."""
    if request.model != SERVED_MODEL:
        raise HTTPException(status_code=404, detail=f"Model '{request.model}' is not served")
    if request.stream:
        raise HTTPException(status_code=400, detail="Streaming is not supported by this mock")
    validate_video_message(request.messages)
    if FIXED_LABEL not in ACTIVITY_LABELS:
        raise HTTPException(status_code=500, detail="Configured mock label is invalid")
    await asyncio.sleep(max(DELAY_MS, 0) / 1000)
    completion_id = f"mock-{uuid4()}"
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(datetime.now(UTC).timestamp()),
        "model": SERVED_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"The best answer is: {FIXED_LABEL}",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 128, "completion_tokens": 6, "total_tokens": 134},
    }
