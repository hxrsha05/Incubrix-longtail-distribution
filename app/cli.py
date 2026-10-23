"""Batch runner: a command-line tool that runs a folder or file of test inputs against the API and writes every result to one folder.

  python -m app.cli run --input samples/inputs --output test-pack/batch-run [--api http://localhost:8000] [--token T]
  python -m app.cli certify --output test-pack/batch-certification [--connector discourse]

--input may be a folder of *.json files, one .json file (an object or a list of objects) or a .jsonl file (one object per line).
An input looks like:
  {"destination_id": "mock-discourse-forum", "rendition": {"title": "...", "body": "..."}, "idempotency_key": "optional"}
Each input is sent to POST /api/v1/publish with an Idempotency-Key (the one in the input, else the file name plus a hash of its content), so
running the same folder twice never posts twice, while an edited file is a new post. The destination must already be in the catalogue (scripts/seed_catalogue.py). Credentials
stay on the server. Output: one <name>.result.json per input plus summary.json. Exit code 0 when no input failed.
The API address and token can also come from INCUBRIX_API and INCUBRIX_TOKEN.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx


def load_inputs(source: Path) -> list[tuple[str, Any]]:
    """Returns [(name, input)]. A name is used for the result file and as the default idempotency key."""
    if source.is_dir():
        files = sorted(source.glob("*.json")) + sorted(source.glob("*.jsonl"))
        items: list[tuple[str, Any]] = []
        for f in files:
            items += _load_file(f)
        return items
    if source.is_file():
        return _load_file(source)
    raise SystemExit(f"input not found: {source}")


def _load_file(path: Path) -> list[tuple[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix == ".jsonl":
            return [(f"{path.stem}-{i:04d}", json.loads(line)) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [(path.stem, {"__bad_input__": f"not valid JSON: {exc.msg} (line {exc.lineno})"})]
    if isinstance(data, list):
        return [(f"{path.stem}-{i:04d}", item) for i, item in enumerate(data, 1)]
    return [(path.stem, data)]


def default_key(name: str, spec: dict) -> str:
    """Same file, same content -> same key (a re-run is a replay). Edit the file -> a new key (it is a new post)."""
    digest = hashlib.sha1(json.dumps({"d": spec["destination_id"], "r": spec["rendition"]}, sort_keys=True).encode()).hexdigest()[:10]
    return f"{name}-{digest}"


def run_inputs(http: httpx.Client, inputs: list[tuple[str, Any]], output_dir: Path, tailoring: bool = True) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    started, results = time.time(), []
    for name, spec in inputs:
        t0 = time.time()
        entry: dict[str, Any] = {"input": name}
        if not isinstance(spec, dict) or "__bad_input__" in spec or "destination_id" not in spec or "rendition" not in spec:
            reason = spec.get("__bad_input__") if isinstance(spec, dict) else None
            entry.update(status="failed", error={"code": "VALIDATION_ERROR", "message": reason or "an input needs destination_id and rendition", "retryable": False, "details": {}})
        else:
            body = {"destination_id": spec["destination_id"], "rendition": spec["rendition"], "tailor": tailoring}
            try:
                resp = http.post("/api/v1/publish", json=body, headers={"Idempotency-Key": str(spec.get("idempotency_key") or default_key(name, spec))}, timeout=60)
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                entry.update(status="failed", error={"code": "PLATFORM_UNAVAILABLE", "message": f"the API could not be reached or answered badly ({type(exc).__name__})", "retryable": True, "details": {}})
            else:
                if resp.status_code == 200:
                    entry.update(status="published", replayed=bool(data.get("replayed")) or resp.headers.get("Idempotent-Replayed") == "true", result=data["result"], tailoring=data.get("tailoring"), manifest=data.get("manifest"))
                else:
                    err = data.get("error", {"code": "INTERNAL_ERROR", "message": resp.text[:200], "retryable": False, "details": {}})
                    entry.update(status="blocked" if err["code"].startswith("DEST_") else "failed", error=err, http_status=resp.status_code)
        entry["duration_s"] = round(time.time() - t0, 3)
        (output_dir / f"{name}.result.json").write_text(json.dumps(entry, indent=2, sort_keys=True), encoding="utf-8")
        results.append({k: entry[k] for k in ("input", "status", "duration_s")})
    summary = {"inputs": len(inputs), "published": sum(r["status"] == "published" for r in results), "blocked": sum(r["status"] == "blocked" for r in results),
               "failed": sum(r["status"] == "failed" for r in results), "duration_s": round(time.time() - started, 3), "results": results}
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def certify_all(http: httpx.Client, output_dir: Path, connector: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"connector_key": connector} if connector else {}
    job = http.post("/api/v1/jobs", json={"kind": "certify_batch", "params": params}, headers={"Idempotency-Key": f"cli-certify-{int(time.time())}"}).json()
    while True:
        state = http.get(f"/api/v1/jobs/{job['job_id']}").json()
        if state["status"] in ("succeeded", "failed"):
            break
        time.sleep(1)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "certification-summary.json").write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    if state["status"] != "succeeded":
        return {"passed": 0, "failed": 1, "inconclusive": 0, "skipped": [], "error": state["error"]}
    return state["result"]


def make_client(api: str, token: str | None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=api, headers=headers, timeout=60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=os.environ.get("INCUBRIX_API", "http://localhost:8000"), help="address of the running service")
    parser.add_argument("--token", default=os.environ.get("INCUBRIX_TOKEN"), help="access token, if the service is protected")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="publish every input through the API")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--no-tailor", action="store_true", help="publish the content exactly as written")
    cert = sub.add_parser("certify", help="certify catalogue destinations through the API")
    cert.add_argument("--output", required=True, type=Path)
    cert.add_argument("--connector")
    args = parser.parse_args(argv)

    http = make_client(args.api, args.token)
    try:
        if args.cmd == "run":
            summary = run_inputs(http, load_inputs(args.input), args.output, tailoring=not args.no_tailor)
            print(f"{summary['inputs']} inputs: {summary['published']} published, {summary['blocked']} blocked by rules, {summary['failed']} failed -> {args.output}")
            return 0 if summary["failed"] == 0 else 1
        out = certify_all(http, args.output, args.connector)
        print(f"certified: {out['passed']} passed, {out['failed']} failed, {out['inconclusive']} inconclusive, {len(out['skipped'])} skipped -> {args.output}")
        return 0 if out["failed"] == 0 else 1
    except httpx.HTTPError as exc:
        print(f"cannot reach the API at {args.api}: {type(exc).__name__}. Is it running (docker compose up -d)?", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
