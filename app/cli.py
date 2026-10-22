"""Command-line runner.

  python -m app.cli run --input inputs/ --output results/       publish every *.json request in a folder
  python -m app.cli certify --output results/ [--connector X]    certify the destinations in the catalogue

An input file looks like:
  {"destination_id": "mock-discourse-forum", "rendition": {"title": "...", "body": "..."}, "idempotency_key": "optional"}
The destination must already be in the catalogue (see scripts/seed_catalogue.py). Credentials come from the same place as
in the service (CONNECTIONS_FILE, or the mock platforms in simulation mode). Every result goes into ONE output folder:
one <name>.result.json per input, plus summary.json. Re-running a folder is safe: the idempotency key is the file name
unless the file sets its own, so a post that already went out is replayed, never duplicated.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.errors import IncuBrixError
from app.models import PublishRequest, Rendition
from app.output_manifest import build_manifest


def run_folder(app, input_dir: Path, output_dir: Path) -> dict[str, Any]:
    state = app.state
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(input_dir.glob("*.json"))
    started, results = time.time(), []
    for file in files:
        t0 = time.time()
        entry: dict[str, Any] = {"input": file.name}
        try:
            spec = json.loads(file.read_text(encoding="utf-8"))
            destination = state.catalogue.destination(spec["destination_id"])
            connection = state.certification.credentials.for_destination(destination)
            request = PublishRequest(rendition=Rendition.model_validate(spec["rendition"]), destination=destination, connection=connection,
                                     idempotency_key=spec.get("idempotency_key") or file.stem)
            result, replayed = state.publish_service.publish(destination.connector_key, request)
            entry.update(status="published", replayed=replayed, result=result)
        except IncuBrixError as exc:
            entry.update(status="blocked" if exc.code.value.startswith("DEST_") else "failed", error=exc.to_body()["error"])
        except (KeyError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            entry.update(status="failed", error={"code": "VALIDATION_ERROR", "message": f"bad input file: {type(exc).__name__}: {str(exc)[:200]}", "retryable": False})
        entry["duration_s"] = round(time.time() - t0, 3)
        entry["manifest"] = build_manifest(kind="publish", started_at=t0, finished_at=time.time(), settings={"simulation_mode": state.certification.simulation})
        (output_dir / f"{file.stem}.result.json").write_text(json.dumps(entry, indent=2, sort_keys=True), encoding="utf-8")
        results.append({k: entry[k] for k in ("input", "status", "duration_s")})
    summary = {"inputs": len(files), "published": sum(r["status"] == "published" for r in results),
               "blocked": sum(r["status"] == "blocked" for r in results), "failed": sum(r["status"] == "failed" for r in results),
               "duration_s": round(time.time() - started, 3), "results": results}
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def certify_all(app, output_dir: Path, connector: str | None) -> dict[str, Any]:
    ids = [r["id"] for r in app.state.catalogue.list(connector_key=connector, limit=1000)["items"]]
    out = app.state.certification.certify_many(ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "certification-summary.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="publish every *.json request in a folder")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    cert = sub.add_parser("certify", help="certify catalogue destinations")
    cert.add_argument("--output", required=True, type=Path)
    cert.add_argument("--connector")
    args = parser.parse_args(argv)

    from app.main import create_app  # imported late so --help works without a database

    app = create_app()
    if args.cmd == "run":
        summary = run_folder(app, args.input, args.output)
        print(f"{summary['inputs']} inputs: {summary['published']} published, {summary['blocked']} blocked by rules, {summary['failed']} failed -> {args.output}")
        return 0 if summary["failed"] == 0 else 1
    out = certify_all(app, args.output, args.connector)
    print(f"certified: {out['passed']} passed, {out['failed']} failed, {out['inconclusive']} inconclusive, {len(out['skipped'])} skipped -> {args.output}")
    return 0 if out["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
