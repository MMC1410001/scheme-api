"""Settings come from .env.<APP_ENV> (default local); environment variables win over the file."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOW = "from app.config import ENV_FILE, get_settings as g; s = g(); print(ENV_FILE, s.api_key, s.docs_enabled)"


def settings_in(folder: Path, **env: str) -> str:
    clean = {k: v for k, v in os.environ.items() if k not in {"APP_ENV", "API_KEY", "DOCS_ENABLED"}}
    result = subprocess.run([sys.executable, "-c", SHOW], cwd=folder, capture_output=True, text=True, check=True,
                            env={**clean, "PYTHONPATH": str(ROOT), **env})
    return result.stdout.strip()


def test_app_env_picks_the_file(tmp_path):
    (tmp_path / ".env.local").write_text("API_KEY=local-key\n")
    (tmp_path / ".env.uat").write_text("API_KEY=uat-key\nDOCS_ENABLED=false\n")
    assert settings_in(tmp_path) == ".env.local local-key True"
    assert settings_in(tmp_path, APP_ENV="uat") == ".env.uat uat-key False"


def test_environment_variables_win_over_the_file(tmp_path):
    (tmp_path / ".env.prod").write_text("API_KEY=from-file\n")
    assert settings_in(tmp_path, APP_ENV="prod", API_KEY="from-platform") == ".env.prod from-platform True"
