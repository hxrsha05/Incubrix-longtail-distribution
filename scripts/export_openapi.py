"""Write openapi.yaml from the running application's own schema.   python -m scripts.export_openapi

tests/test_openapi.py fails if the committed file no longer matches the service, so the two cannot drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

OUT = Path(__file__).resolve().parent.parent / "openapi.yaml"


def current_schema() -> dict:
    from app.main import create_app

    return json.loads(json.dumps(create_app().openapi()))  # plain JSON types only


def main() -> None:
    OUT.write_text(yaml.safe_dump(current_schema(), sort_keys=True, allow_unicode=True), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
