import os

import pytest

# Unit tests run with auth off, which the API allows only with APP_ENV=local set explicitly.
os.environ.setdefault("APP_ENV", "local")

from app.config import Settings, get_settings
from app.main import app
from app.ratelimit import RateLimitMiddleware


@pytest.fixture(autouse=True)
def ignore_local_env():
    """Unit tests must not depend on the developer's .env (for example AUTH_CLIENTS); auth is off here.

    Tests that need specific settings override get_settings themselves.
    """
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture(autouse=True)
def isolated_rate_limits(monkeypatch):
    """The app's limiters are built at import from the developer's .env (REDIS_URL), and TestClient
    always calls from the same address. Count in memory and from zero in each test, so results don't
    depend on other tests, other pytest runs or a running Redis."""
    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    layer = app.middleware_stack
    while layer is not None:
        if isinstance(layer, RateLimitMiddleware):
            monkeypatch.setattr(layer, "redis_url", None)
            monkeypatch.setattr(layer, "_counts", {})
        layer = getattr(layer, "app", None)
