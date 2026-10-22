"""openapi.yaml must describe the service as it is now."""
from pathlib import Path

import yaml

from scripts.export_openapi import current_schema

ROOT = Path(__file__).resolve().parent.parent


def test_committed_openapi_matches_the_running_service():
    committed = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    assert committed == current_schema(), "openapi.yaml is out of date: run  python -m scripts.export_openapi"


def test_every_route_of_the_service_is_documented():
    paths = set(yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))["paths"])
    for needed in ("/connector/v1/{connector_key}/{method}", "/api/v1/jobs", "/api/v1/jobs/{job_id}", "/api/v1/destinations",
                   "/api/v1/destinations/{dest_id}/certify", "/health", "/ready"):
        assert needed in paths
