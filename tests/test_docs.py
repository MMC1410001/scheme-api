"""Swagger UI at /docs (app/docs.py): one pinned swagger-ui-dist release, checked with SRI (QA 2026-10-16 #14)."""
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app import docs
from app.main import app
from tests.test_config import ROOT


@pytest.fixture
def client():
    return TestClient(app)


def test_swagger_ui_loads_a_pinned_release_with_integrity_hashes(client):
    response = client.get("/docs")
    assert response.status_code == 200
    html = response.text
    assert re.fullmatch(r"\d+\.\d+\.\d+", docs.SWAGGER_UI_VERSION)  # exact, never "@5"
    assert "swagger-ui-dist@5/" not in html
    assert (f'<script src="{docs.SWAGGER_JS_URL}" integrity="{docs.SWAGGER_JS_SRI}" crossorigin="anonymous">'
            in html)
    assert f'href="{docs.SWAGGER_CSS_URL}" integrity="{docs.SWAGGER_CSS_SRI}" crossorigin="anonymous">' in html
    assert "url: '/openapi.json'" in html and "/docs/oauth2-redirect" in html


def test_docs_csp_allows_only_the_pinned_release(client):
    csp = client.get("/docs").headers["content-security-policy"]
    assert f"script-src 'self' 'unsafe-inline' {docs.SWAGGER_UI_BASE};" in csp
    assert "https://cdn.jsdelivr.net;" not in csp and "https://cdn.jsdelivr.net " not in csp


def test_oauth2_redirect_page_is_served_and_kept_out_of_the_spec(client):
    assert client.get("/docs/oauth2-redirect").status_code == 200
    assert not {"/docs", "/docs/oauth2-redirect"} & set(client.get("/openapi.json").json()["paths"])


def test_a_changed_swagger_page_fails_loudly_instead_of_dropping_the_check():
    with pytest.raises(RuntimeError, match="Swagger UI page changed"):
        docs._with_integrity('<script src="https://elsewhere/x.js"></script>')


def test_docs_off_hides_the_page_and_the_spec(tmp_path):
    code = ("from fastapi.testclient import TestClient; from app.main import app; c = TestClient(app); "
            "print(c.get('/docs').status_code, c.get('/docs/oauth2-redirect').status_code, "
            "c.get('/openapi.json').status_code)")
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True,
                            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT), "APP_ENV": "local",
                                 "DOCS_ENABLED": "false"})
    assert result.stdout.split() == ["404", "404", "404"]
