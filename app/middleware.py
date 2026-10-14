"""Security middleware: response headers and a request body size limit."""
from fastapi import HTTPException, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store",  # dealer data must not be cached by proxies or browsers
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
# Swagger UI loads its script and styles from a CDN and starts with an inline script.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)


class SecurityHeadersMiddleware:
    """Add SECURITY_HEADERS to every HTTP response, errors included.

    Plain ASGI rather than BaseHTTPMiddleware, which costs about as much per request as the
    endpoint itself under load.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.headers = [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
        self.docs_headers = [(k, DOCS_CSP.encode() if k == b"content-security-policy" else v) for k, v in self.headers]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = self.docs_headers if scope["path"] == "/docs" else self.headers
        names = {name for name, _ in extra}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in names]
                message = {**message, "headers": kept + extra}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Reject request bodies over max_bytes with 413, whether or not Content-Length is sent."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
            await _too_large(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # FastAPI passes HTTPException through while reading the body.
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Request body too large")
            return message

        await self.app(scope, limited_receive, send)


async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse({"detail": "Request body too large"}, status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    await response(scope, receive, send)
