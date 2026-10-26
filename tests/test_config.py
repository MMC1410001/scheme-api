"""Settings come from .env.<APP_ENV> (default local); environment variables win over the file."""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent
SHOW = "from app.config import ENV_FILE, get_settings as g; s = g(); print(ENV_FILE, s.jwt_ttl_seconds, s.docs_enabled)"


def settings_in(folder: Path, **env: str) -> str:
    clean = {k: v for k, v in os.environ.items() if k not in {"APP_ENV", "JWT_TTL_SECONDS", "DOCS_ENABLED"}}
    result = subprocess.run([sys.executable, "-c", SHOW], cwd=folder, capture_output=True, text=True, check=True,
                            env={**clean, "PYTHONPATH": str(ROOT), **env})
    return result.stdout.strip()


def test_app_env_picks_the_file(tmp_path):
    (tmp_path / ".env.local").write_text("JWT_TTL_SECONDS=111\n")
    (tmp_path / ".env.uat").write_text("JWT_TTL_SECONDS=222\nDOCS_ENABLED=false\n")
    assert settings_in(tmp_path) == ".env.local 111 True"
    assert settings_in(tmp_path, APP_ENV="uat") == ".env.uat 222 False"


def test_environment_variables_win_over_the_file(tmp_path):
    (tmp_path / ".env.prod").write_text("JWT_TTL_SECONDS=333\n")
    assert settings_in(tmp_path, APP_ENV="prod", JWT_TTL_SECONDS="444") == ".env.prod 444 True"


GOOD_AUTH = {"AUTH_CLIENTS": '{"partner": "%s"}' % ("a" * 64), "JWT_SECRET": "s" * 32}


def start_api(folder: Path, **env: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if k not in {"APP_ENV", "AUTH_CLIENTS", "JWT_SECRET"}}
    return subprocess.run([sys.executable, "-c", "import app.main"], cwd=folder, capture_output=True, text=True,
                          env={**clean, "PYTHONPATH": str(ROOT), **env})


@pytest.mark.parametrize("env, problem", [
    ({}, "AUTH_CLIENTS is empty"),
    ({"AUTH_CLIENTS": GOOD_AUTH["AUTH_CLIENTS"]}, "JWT_SECRET is not set"),
    ({**GOOD_AUTH, "JWT_SECRET": "short"}, "JWT_SECRET must be at least 32 characters"),
    ({**GOOD_AUTH, "AUTH_CLIENTS": '{"partner": "plain-secret"}'}, "must be the SHA-256 hex of its secret"),
    ({**GOOD_AUTH, "JWT_SECRET": "local-only-jwt-signing-key-not-for-servers-0001"}, "local example key"),
    ({**GOOD_AUTH, "AUTH_CLIENTS": '{"x": "0b1f90b824765bd860b58a3ebc0396a4f6f4c4ad8f1eed10135c0cb6cf3bd171"}'},
     "local example client"),
])
def test_api_refuses_to_start_outside_local_without_strong_auth(tmp_path, env, problem):
    result = start_api(tmp_path, APP_ENV="prod", **env)
    assert result.returncode != 0 and problem in result.stderr


def test_api_starts_with_auth_settings_or_locally_without_any(tmp_path):
    assert start_api(tmp_path, APP_ENV="prod", **GOOD_AUTH).returncode == 0
    assert start_api(tmp_path, APP_ENV="prod", **GOOD_AUTH, JWT_PREVIOUS_SECRET="").returncode == 0  # empty = unset
    assert start_api(tmp_path, APP_ENV="local").returncode == 0  # local, auth off
    assert start_api(tmp_path, APP_ENV="local", JWT_SECRET="short").returncode != 0  # local, but half configured
    assert start_api(tmp_path, **GOOD_AUTH).returncode == 0  # APP_ENV unset, auth on
    assert start_api(tmp_path, APP_ENV="local", **EXAMPLE_AUTH).returncode == 0  # .env.example values, locally


EXAMPLE_AUTH = {  # the published .env.example client and key
    "AUTH_CLIENTS": '{"local-client": "0b1f90b824765bd860b58a3ebc0396a4f6f4c4ad8f1eed10135c0cb6cf3bd171"}',
    "JWT_SECRET": "local-only-jwt-signing-key-not-for-servers-0001",
}


@pytest.mark.parametrize("env, problem", [
    (EXAMPLE_AUTH, "local example client"),
    ({**EXAMPLE_AUTH, "AUTH_CLIENTS": GOOD_AUTH["AUTH_CLIENTS"]}, "local example key"),
    ({**EXAMPLE_AUTH, "JWT_SECRET": GOOD_AUTH["JWT_SECRET"]}, "local example client"),
])
def test_example_credentials_need_app_env_local_set_explicitly(tmp_path, env, problem):
    """QA 2026-10-16 #7: a server that forgets APP_ENV must not run on credentials anyone can read."""
    result = start_api(tmp_path, **env)
    assert result.returncode != 0
    assert problem in result.stderr and "APP_ENV=(not set)" in result.stderr and "set APP_ENV=local" in result.stderr


def test_trusted_proxies_are_read_as_a_comma_separated_list(monkeypatch):
    """QA 2026-10-16 #1."""
    monkeypatch.setenv("TRUSTED_PROXIES", " 10.0.0.5, 172.18.0.0/16 ,::1")
    assert Settings(_env_file=None).trusted_proxies == ["10.0.0.5", "172.18.0.0/16", "::1"]
    monkeypatch.setenv("TRUSTED_PROXIES", "")
    assert Settings(_env_file=None).trusted_proxies == []


def test_a_bad_trusted_proxy_stops_the_api(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5,nginx")
    with pytest.raises(ValueError, match="nginx"):
        Settings(_env_file=None)


def test_api_refuses_to_run_open_when_app_env_is_unset(tmp_path):
    result = start_api(tmp_path)
    assert result.returncode != 0 and "APP_ENV is not set and there are no auth settings" in result.stderr


KEY = {"client_email": "loader@p.iam.gserviceaccount.com", "private_key": "-----BEGIN PRIVATE KEY-----\nsecret\n"}


@pytest.mark.parametrize("value", [base64.b64encode(json.dumps(KEY, indent=2).encode()).decode(), json.dumps(KEY)])
def test_gcs_key_as_base64_or_json(value):
    assert Settings(_env_file=None, gcs_credentials_json=value).gcs_credentials_info == KEY


def test_gcs_key_empty_means_none():
    assert Settings(_env_file=None, gcs_credentials_json="").gcs_credentials_info is None


@pytest.mark.parametrize("value", ["not-base64!", base64.b64encode(b'{"type": "x"}').decode(), '{"private_key": "secret"}'])
def test_bad_gcs_key_stops_start_up_without_showing_it(value):
    with pytest.raises(ValueError, match="GCS_CREDENTIALS_JSON") as error:
        Settings(_env_file=None, gcs_credentials_json=value)
    assert "secret" not in str(error.value)
