import pytest

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture(autouse=True)
def ignore_local_env():
    """Unit tests must not depend on the developer's .env (for example API_KEY).

    Tests that need specific settings override get_settings themselves.
    """
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, api_key=None)
    yield
    app.dependency_overrides.pop(get_settings, None)
