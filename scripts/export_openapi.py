"""Write docs/openapi.json from the app, for clients that import specs (Postman, Swagger, codegen).

    uv run python scripts/export_openapi.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # run as a file, so make the app package importable

from app.main import app  # noqa: E402

path = ROOT / "docs" / "openapi.json"
path.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print("wrote", path.relative_to(path.parent.parent))
