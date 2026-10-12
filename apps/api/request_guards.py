"""Defenses against cross-site requests and DNS rebinding for the unauthenticated local API."""

from collections.abc import Iterable

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

CLIENT_HEADER = "X-Requested-With"
CLIENT_HEADER_VALUE = "fall-detection"
# First-party callers (web client, scripts, tests) send these on every request.
CLIENT_HEADERS = {CLIENT_HEADER: CLIENT_HEADER_VALUE}
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class RequireClientHeaderMiddleware:
    """Refuse state-changing requests that lack the first-party client header.

    A browser sends body-less, form and multipart POSTs cross-origin without a CORS
    preflight. A custom header forces that preflight, which the API does not answer,
    so another site cannot trigger uploads, GPU warm-ups or other changes. GET stays
    header-free so `<img>`, `<video>` and download links keep working.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the next ASGI application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject an unsafe method without the header before the body is read."""
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            expected = CLIENT_HEADER_VALUE.encode("latin-1")
            name = CLIENT_HEADER.lower().encode("latin-1")
            values = [value for key, value in scope["headers"] if key == name]
            if values != [expected]:
                response = JSONResponse(
                    {"detail": f"Missing {CLIENT_HEADER}: {CLIENT_HEADER_VALUE} header"},
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def install_request_guards(app: FastAPI, allowed_hosts: Iterable[str]) -> None:
    """Add the client-header check and the `Host` allow-list (outermost) to the app."""
    app.add_middleware(RequireClientHeaderMiddleware)
    # Starlette compares the host name without its port, so any port is accepted.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
