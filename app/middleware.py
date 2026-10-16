"""Request middleware: security headers, a body size limit, and leniency for zero-padded JSON numbers."""
import json
import re

from fastapi import HTTPException, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.docs import SWAGGER_UI_BASE

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store",  # dealer data must not be cached by proxies or browsers
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
# Swagger UI loads its script and styles from one pinned CDN release (app.docs, which also adds
# integrity hashes) and starts with an inline script.
DOCS_CSP = (
    f"default-src 'self'; script-src 'self' 'unsafe-inline' {SWAGGER_UI_BASE}; "
    f"style-src 'self' 'unsafe-inline' {SWAGGER_UI_BASE}; "
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


# A JSON string (kept as is) or the "0" of a bare 01-09. Longer padding (009), signs (-09) and
# decimals (09.5) are left alone, so those bodies stay invalid JSON.
_STRING_OR_PADDING = re.compile(r'"(?:[^"\\]|\\.)*"|(?<![\w.\-])0(?=[1-9](?![\w.]))')


def strip_number_padding(body: bytes) -> bytes | None:
    """Return body with 01-09 written as 1-9 if that makes it valid JSON, else None."""
    try:
        text = body.decode()
    except UnicodeDecodeError:
        return None
    fixed = _STRING_OR_PADDING.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text)
    if fixed == text:
        return None
    try:
        json.loads(fixed)
    except ValueError:
        return None
    return fixed.encode()


class LenientJsonNumbersMiddleware:
    """Accept a zero-padded month such as "month": 09, which strict JSON rejects.

    Bodies that already parse are passed through untouched; only an invalid JSON body is
    rewritten, and only if stripping the padding makes it valid.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Media types are case-insensitive (RFC 9110): Application/JSON is JSON too.
        content_type = dict(scope.get("headers", [])).get(b"content-type", b"").lower()
        if scope["type"] != "http" or not content_type.startswith(b"application/json"):
            await self.app(scope, receive, send)
            return

        chunks = []
        while True:
            try:
                message = await receive()
            except HTTPException as exc:  # the size limit, raised outside FastAPI's handlers here
                await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
                return
            if message["type"] != "http.request":  # client disconnected
                await self.app(scope, _replay([message], receive), send)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        try:
            json.loads(body)
        except ValueError:
            body = strip_number_padding(body) or body
        await self.app(scope, _replay([{"type": "http.request", "body": body, "more_body": False}], receive), send)


def _replay(messages: list[Message], receive: Receive) -> Receive:
    async def replayed() -> Message:
        return messages.pop(0) if messages else await receive()
    return replayed
