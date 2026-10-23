"""Runs a CSV import as a background job and stores its files (input, row-by-row report) with download links."""
from __future__ import annotations

import json
from typing import Any

from app.csv_import import Importer, import_id_for, report_to_csv
from app.errors import ErrorCode, IncuBrixError
from app.evidence import EvidenceStore


def register_import_job(jobs, importer: Importer, store: EvidenceStore) -> None:
    def import_job(ctx, params: dict[str, Any]) -> dict[str, Any]:
        import_id = params.get("import_id")
        path = store.read(f"{import_id}/input.csv") if import_id else None
        if path is None:
            raise IncuBrixError(ErrorCode.NOT_FOUND, f"no uploaded CSV for import {import_id!r}")
        result = importer.run(path.read_text(encoding="utf-8"), commit=bool(params.get("commit", True)), progress=ctx.progress)
        report = result.pop("report")
        links = {
            "input_csv": store.save(import_id, "input.csv", path.read_text(encoding="utf-8")),
            "report_csv": store.save(import_id, "report.csv", report_to_csv(report)),
            "report_json": store.save(import_id, "report.json", {"summary": {k: v for k, v in result.items() if k != "manifest"}, "rows": report, "manifest": result["manifest"]}),
        }
        result["files"] = {name: info["download_url"] for name, info in links.items()}
        result["import_id"] = import_id
        return json.loads(json.dumps(result, default=str))

    jobs.register("import", import_job)
