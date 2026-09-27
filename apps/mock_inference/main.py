import asyncio
import base64
import binascii
import io
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.mock_inference.scenarios import fingerprint, load_manifest, next_attempt

MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_FRAMES = 32
MAX_FRAME_BYTES = 1024 * 1024
MAX_FRAME_PIXELS = 672 * 672


class RequestSizeLimit:
    """Bound completion bodies before JSON parsing, including chunked requests."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the ASGI application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Buffer at most the supported request size before handing off."""
        if scope["type"] != "http" or scope["path"] != "/v1/chat/completions":
            await self.app(scope, receive, send)
            return
        body: bytearray | None = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                await JSONResponse({"detail": "Mock request byte limit exceeded"}, 413)(
                    scope, receive, send
                )
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            nonlocal body
            if body is not None:
                data = bytes(body)
                body = None
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


MANIFEST = load_manifest()
app = FastAPI(title="Mock vLLM inference", version="0.1.0")
app.add_middleware(RequestSizeLimit)


class ChatCompletionRequest(BaseModel):
    """Supported subset of the OpenAI-compatible completion request."""

    model_config = ConfigDict(extra="forbid")
    media_io_kwargs: dict[str, Any] | None = None
    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    temperature: float = Field(default=0, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=32, gt=0)
    stream: bool = False


def validate_video_message(messages: list[dict[str, Any]]) -> None:
    """Validate the exact multimodal subset the application depends on."""
    if len(messages) != 1 or messages[0].get("role") != "user":
        raise HTTPException(status_code=400, detail="Exactly one user message is required")
    content = messages[0].get("content")
    if not isinstance(content, list):
        raise HTTPException(status_code=400, detail="Message content must be a list")
    if len(content) != 2 or any(
        not isinstance(part, dict) or not isinstance(part.get("type"), str) for part in content
    ):
        raise HTTPException(
            status_code=400, detail="Exactly one text and video_url part are required"
        )
    content_types = {part.get("type") for part in content}
    if content_types != {"text", "video_url"}:
        raise HTTPException(status_code=400, detail="Text and video_url content are required")
    text_part = next(part for part in content if part["type"] == "text")
    if not isinstance(text_part.get("text"), str) or not text_part["text"].strip():
        raise HTTPException(status_code=400, detail="Non-empty text is required")
    if set(messages[0]) != {"role", "content"} or set(text_part) != {"type", "text"}:
        raise HTTPException(400, "Unsupported message or text fields")
    video_parts = [part for part in content if part.get("type") == "video_url"]
    video_url = video_parts[0].get("video_url") if video_parts else None
    if (
        not isinstance(video_url, dict)
        or set(video_url) != {"url"}
        or set(video_parts[0]) != {"type", "video_url"}
        or not isinstance(video_url.get("url"), str)
    ):
        raise HTTPException(status_code=400, detail="video_url.url is required")


def validate_prepared_video(request: ChatCompletionRequest) -> dict[str, object]:
    """Decode JPEG bytes and enforce the application metadata subset."""
    url = next(part for part in request.messages[0]["content"] if part["type"] == "video_url")[
        "video_url"
    ]["url"]
    if (
        url
        == "data:video/mp4;base64,"
        + base64.b64encode(b"fall-detection-synthetic-corridor-v1").decode()
    ):
        if request.media_io_kwargs is not None:
            raise HTTPException(400, "Synthetic demo must not carry prepared metadata")
        return {"kind": "synthetic-demo", "bytes": "fall-detection-synthetic-corridor-v1"}
    if not url.startswith("data:video/jpeg;base64,"):
        raise HTTPException(400, "Only prepared JPEG sequences or the synthetic demo are supported")
    frames = url.removeprefix("data:video/jpeg;base64,").split(",")
    if len(frames) > MAX_FRAMES:
        raise HTTPException(413, "Mock frame count limit exceeded")
    hashes = []
    sizes = []
    try:
        for frame in frames:
            if len(frame) > 4 * ((MAX_FRAME_BYTES + 2) // 3):
                raise HTTPException(413, "Mock encoded frame byte limit exceeded")
            data = base64.b64decode(frame, validate=True)
            if len(data) > MAX_FRAME_BYTES:
                raise HTTPException(413, "Mock frame byte limit exceeded")
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG":
                    raise ValueError("Frame is not JPEG")
                if image.width * image.height > MAX_FRAME_PIXELS:
                    raise HTTPException(413, "Mock decoded frame pixel limit exceeded")
                sizes.append(image.size)
                image.load()
            hashes.append(fingerprint(data.hex()))
    except (
        ValueError,
        binascii.Error,
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
    ) as exc:
        raise HTTPException(400, "Invalid prepared JPEG frame") from exc
    metadata = request.media_io_kwargs
    if not isinstance(metadata, dict) or set(metadata) != {"video"}:
        raise HTTPException(400, "Prepared video metadata is required")
    video = metadata["video"]
    required = {"fps", "frames_indices", "total_num_frames", "duration", "do_sample_frames"}
    if not isinstance(video, dict) or set(video) != required:
        raise HTTPException(400, "Unsupported or missing video metadata fields")
    if (
        type(video["total_num_frames"]) is not int
        or video["total_num_frames"] != len(frames)
        or video["frames_indices"] != list(range(len(frames)))
        or any(type(index) is not int for index in video["frames_indices"])
        or video["do_sample_frames"] is not False
        or any(
            type(video[k]) not in (int, float) or not math.isfinite(video[k]) or video[k] <= 0
            for k in ("fps", "duration")
        )
        or len(set(sizes)) != 1
    ):
        raise HTTPException(400, "Prepared video metadata or frame count is inconsistent")
    return {
        "kind": "prepared-jpeg",
        "frames": hashes,
        "metadata": {**video, "fps": float(video["fps"]), "duration": float(video["duration"])},
    }


@app.get("/v1/mock/identity")
def identity() -> dict[str, object]:
    """Discover complete effective fixture settings without production control fields."""
    manifest = MANIFEST
    return {
        "backend": "mock",
        "fixture_version": manifest.identity(),
        "manifest": manifest.model_dump(),
    }


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
                "id": MANIFEST.model,
                "object": "model",
                "created": 0,
                "owned_by": "fall-detection-mock",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest, x_mock_fixture_identity: str | None = Header(default=None)
) -> Any:
    """Return one delayed thesis-format response after contract validation."""
    manifest = MANIFEST
    if request.model != manifest.model:
        raise HTTPException(status_code=404, detail=f"Model '{request.model}' is not served")
    if request.stream:
        raise HTTPException(status_code=400, detail="Streaming is not supported by this mock")
    validate_video_message(request.messages)
    input_key = fingerprint(await run_in_threadpool(validate_prepared_video, request))
    if x_mock_fixture_identity is not None and x_mock_fixture_identity != manifest.identity():
        raise HTTPException(409, "Required mock fixture identity is unavailable")
    normalized = request.model_dump(exclude={"messages", "media_io_kwargs"})
    normalized["input"] = input_key
    normalized["prompt"] = next(
        part for part in request.messages[0]["content"] if part["type"] == "text"
    )["text"]
    request_key = fingerprint(normalized)
    scenario = manifest.requests.get(request_key, manifest.inputs.get(input_key, manifest.default))
    if scenario.fail_first:
        state_path = os.getenv("MOCK_INFERENCE_ATTEMPT_DB")
        if not state_path:
            raise HTTPException(
                503, "Fail-then-succeed fixtures require a durable attempt database"
            )
        if next_attempt(Path(state_path), manifest.identity(), request_key) > scenario.fail_first:
            scenario = scenario.model_copy(update={"fault": "none"})
    await asyncio.sleep(scenario.delay_ms / 1000)
    headers = {
        "X-Mock-Fixture-Identity": manifest.identity(),
        "X-Mock-Input-Fingerprint": input_key,
        "X-Mock-Request-Fingerprint": request_key,
    }
    if scenario.fault in {"429", "500", "503"}:
        return JSONResponse(
            {"error": {"message": "Scripted mock HTTP failure"}},
            status_code=int(scenario.fault),
            headers=headers,
        )
    if scenario.fault == "malformed":
        return JSONResponse({"choices": []}, headers=headers)
    if scenario.fault == "interruption":

        async def interrupted():
            yield b'{"choices":'
            raise OSError("Scripted mock stream interruption")

        return StreamingResponse(interrupted(), media_type="application/json", headers=headers)
    output = {
        "invalid": "The best answer is: flying",
        "ambiguous": "The best answer is: fall or fallen",
        "truncated": "The best answer is:",
    }.get(
        scenario.fault,
        scenario.output if scenario.output is not None else f"The best answer is: {scenario.label}",
    )
    completion_id = f"mock-{uuid4()}"
    return JSONResponse(
        {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(datetime.now(UTC).timestamp()),
            "model": manifest.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": output,
                    },
                    "finish_reason": "length" if scenario.fault == "truncated" else "stop",
                }
            ],
            "usage": {"prompt_tokens": 128, "completion_tokens": 6, "total_tokens": 134},
        },
        headers=headers,
    )
