"""Write docs/openapi.json from the app, for clients that import specs (Postman, Swagger, codegen).

    uv run python scripts/export_openapi.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # run as a file, so make the app package importable


# Only the spec is read, no server starts: allow it without auth settings (app/main.py refuses to
# import with APP_ENV unset and no AUTH_CLIENTS/JWT_SECRET).
os.environ.setdefault("APP_ENV", "local")
from app.main import app  # noqa: E402

path = ROOT / "docs" / "openapi.json"
path.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print("wrote", path.relative_to(path.parent.parent))
