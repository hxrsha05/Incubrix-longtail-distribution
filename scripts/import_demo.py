"""Upload a CSV to the RUNNING service, wait for the import job, and save the outputs as evidence.

    python -m scripts.import_demo [csv_file] [api_url] [--keep]

Saves test-pack/csv-import/<name>/: input.csv, report.csv, report.json and summary.txt (counts, time, rows per second).
Unless --keep is given, the catalogue rows the import created are deleted again afterwards, so the demo leaves the
catalogue as it was (the 1,000 test sites would otherwise be certified by the next batch run).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
args = [a for a in sys.argv[1:] if not a.startswith("--")]
KEEP = "--keep" in sys.argv
CSV = Path(args[0]) if args else ROOT / "samples" / "catalogue" / "sample-1000.csv"
API = args[1] if len(args) > 1 else "http://localhost:8000"


def main() -> None:
    out_dir = ROOT / "test-pack" / "csv-import" / CSV.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    text = CSV.read_text(encoding="utf-8")
    with httpx.Client(base_url=API, timeout=60) as http:
        before = http.get("/api/v1/destinations?limit=1").json()["total"]
        t0 = time.time()
        job = http.post("/api/v1/imports", content=text, headers={"Content-Type": "text/csv", "Idempotency-Key": f"demo-{CSV.stem}-{int(t0)}"}).json()
        print(f"uploaded {job['rows']} rows, job {job['job_id']}")
        last = -1
        while True:
            state = http.get(f"/api/v1/jobs/{job['job_id']}").json()
            if state["progress"] != last:
                last = state["progress"]
                print(f"  {state['status']} {last}%")
            if state["status"] in ("succeeded", "failed"):
                break
            time.sleep(1)
        wall = time.time() - t0
        if state["status"] != "succeeded":
            raise SystemExit(f"import failed: {state['error']}")
        result = state["result"]
        (out_dir / "input.csv").write_text(text, encoding="utf-8")
        (out_dir / "report.csv").write_text(httpx.get(result["files"]["report_csv"], timeout=60).text, encoding="utf-8")
        (out_dir / "report.json").write_text(httpx.get(result["files"]["report_json"], timeout=60).text, encoding="utf-8")
        summary = [
            f"CSV import of {CSV.name}",
            f"rows: {result['rows']}",
            f"accepted: {result['accepted']}   duplicate: {result['duplicate']}   invalid: {result['invalid']}   unreachable: {result['unreachable']}",
            f"catalogue rows created: {result['catalogue_rows_created']}",
            f"import time (service): {result['duration_s']} s   total wall time: {wall:.1f} s   ({result['rows_per_second']} rows/s)",
            f"limit from the brief: 1,000 rows in under 300 s  ->  {'WITHIN the limit' if wall < 300 else 'OVER the limit'}",
            f"workers: {result['manifest']['settings']['workers']}   cost: ${result['manifest']['cost']['usd']}",
        ]
        after = http.get("/api/v1/destinations?limit=1").json()["total"]
        summary.append(f"catalogue size before: {before}, after the import: {after}")
        if not KEEP:
            removed = 0
            for row in json.loads((out_dir / "report.json").read_text(encoding="utf-8"))["rows"]:
                if row.get("destination_id") and http.delete(f"/api/v1/destinations/{row['destination_id']}").status_code == 204:
                    removed += 1
            summary.append(f"cleanup: removed {removed} imported rows again (run with --keep to leave them in the catalogue)")
        (out_dir / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
        print("\n".join(summary))


if __name__ == "__main__":
    main()
