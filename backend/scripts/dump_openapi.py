"""
Dump du schéma OpenAPI de l'API FastAPI vers un fichier JSON.
Usage :
    python scripts/dump_openapi.py [OUT]   (défaut : ../frontend/src/api/openapi.json)

Pipeline de génération des types TypeScript :
    1) python scripts/dump_openapi.py
    2) cd ../frontend && npx openapi-typescript src/api/openapi.json -o src/api/types.ts
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from api import app  # noqa: E402

_DEFAULT_OUT = _BACKEND_ROOT.parent / "frontend" / "src" / "api" / "openapi.json"


def main(out_path: Path) -> None:
    schema = app.openapi()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    paths = schema.get("paths", {})
    endpoints = sum(len(m) for m in paths.values())
    print(f"[dump_openapi] {len(paths)} routes × {endpoints} endpoints → {out_path}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_OUT
    main(out)
