from collections.abc import Callable

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Space for the multipart boundary and headers in addition to the configured file limit.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


class UploadBodyLimitMiddleware:
    """Bound upload request bytes before multipart parsing buffers file content."""

    def __init__(self, app: ASGIApp, upload_max_bytes: Callable[[], int]) -> None:
        """Read the current file limit for each upload request."""
        self.app = app
        self.upload_max_bytes = upload_max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject an oversized upload as soon as a received chunk crosses the limit."""
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/videos":
            await self.app(scope, receive, send)
            return

        max_body_bytes = self.upload_max_bytes() + MULTIPART_OVERHEAD_BYTES
        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > max_body_bytes:
                    raise HTTPException(
                        status_code=413, detail="Upload request exceeds the configured byte limit"
                    )
            return message

        await self.app(scope, limited_receive, send)
