"""Swagger UI at /docs, loaded from one pinned swagger-ui-dist release with Subresource Integrity.

FastAPI's built-in page loads swagger-ui-dist@5 (whatever 5.x is newest) with no integrity check, so a
changed or compromised CDN file would run on the docs page. Here the version is exact and the browser
refuses the files unless their SHA-384 matches. To upgrade: pick a release, download its
swagger-ui-bundle.js and swagger-ui.css, and replace the version and both hashes
(`openssl dgst -sha384 -binary <file> | openssl base64 -A`).
"""
from fastapi import APIRouter, Request
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import HTMLResponse

SWAGGER_UI_VERSION = "5.32.15"
SWAGGER_UI_BASE = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/"
SWAGGER_JS_URL = SWAGGER_UI_BASE + "swagger-ui-bundle.js"
SWAGGER_CSS_URL = SWAGGER_UI_BASE + "swagger-ui.css"
SWAGGER_JS_SRI = "sha384-m7zaGj7MPzU+G4lz2eyy73GxK9bbRDr9bB2CSdj8wodg2wu/Wnt6wsoLP3JD+RS9"
SWAGGER_CSS_SRI = "sha384-fgyWYkUAamzuI8mJFu/xpRP0JWCJRwkwUwsYDoOYVHUJ8NQE5cENn8ib3ppwFFSX"
DOCS_URL = "/docs"
OAUTH2_REDIRECT_URL = "/docs/oauth2-redirect"

docs_router = APIRouter(include_in_schema=False)


def _with_integrity(html: str) -> str:
    """Add integrity and crossorigin to the script and stylesheet tags; fail if they are not found,
    so a FastAPI upgrade that changes its page cannot silently drop the check."""
    replacements = {
        f'<script src="{SWAGGER_JS_URL}">':
            f'<script src="{SWAGGER_JS_URL}" integrity="{SWAGGER_JS_SRI}" crossorigin="anonymous">',
        f'href="{SWAGGER_CSS_URL}">':
            f'href="{SWAGGER_CSS_URL}" integrity="{SWAGGER_CSS_SRI}" crossorigin="anonymous">',
    }
    for old, new in replacements.items():
        if html.count(old) != 1:
            raise RuntimeError(f"Swagger UI page changed: expected one {old!r}")
        html = html.replace(old, new)
    return html


@docs_router.get(DOCS_URL)
def swagger_ui(request: Request) -> HTMLResponse:
    app = request.app
    page = get_swagger_ui_html(
        openapi_url=app.openapi_url, title=f"{app.title} - Swagger UI",
        swagger_js_url=SWAGGER_JS_URL, swagger_css_url=SWAGGER_CSS_URL,
        oauth2_redirect_url=OAUTH2_REDIRECT_URL,
        init_oauth=app.swagger_ui_init_oauth, swagger_ui_parameters=app.swagger_ui_parameters,
    )
    return HTMLResponse(_with_integrity(page.body.decode()))


@docs_router.get(OAUTH2_REDIRECT_URL)
def swagger_ui_redirect() -> HTMLResponse:
    return get_swagger_ui_oauth2_redirect_html()
