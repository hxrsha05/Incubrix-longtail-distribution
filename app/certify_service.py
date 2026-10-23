"""Ties the pieces together: catalogue row -> connector + credentials -> certify() -> evidence + status change."""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from app.catalogue import Catalogue
from app.certification import certify, run_summary
from app.connectors.registry import ConnectorRegistry
from app.credentials import CredentialProvider
from app.errors import ErrorCode, IncuBrixError
from app.evidence import EvidenceStore
from app.output_manifest import build_manifest


class CertificationService:
    def __init__(self, catalogue: Catalogue, registry: ConnectorRegistry, credentials: CredentialProvider, evidence: EvidenceStore,
                 *, ttl_s: float, simulation: bool, max_consecutive_failures: int = 3, rate_limit_wait_cap_s: float = 30,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.catalogue, self.registry, self.credentials, self.evidence = catalogue, registry, credentials, evidence
        self.ttl_s, self.simulation, self.max_failures = ttl_s, simulation, max_consecutive_failures
        self.rate_limit_wait_cap_s, self.clock, self.sleep = rate_limit_wait_cap_s, clock, sleep

    def certify_destination(self, dest_id: str) -> dict[str, Any]:
        row = self.catalogue.get(dest_id)
        destination = self.catalogue.destination(dest_id)
        try:
            connector = self.registry.get(destination.connector_key)
            connection = self.credentials.for_destination(destination)
        except IncuBrixError as exc:  # unknown connector, disabled connector, no credentials: record it, do not crash
            record = self._unrunnable(row, exc)
        else:
            record = certify(connector, destination, connection, destination_id=dest_id, clock=self.clock, sleep=self.sleep,
                             ttl_s=self.ttl_s, rate_limit_wait_cap_s=self.rate_limit_wait_cap_s, simulation=self.simulation)
        record["evidence"] = {
            "record": self.evidence.save(dest_id, f"run-{record['run_id']}.json", record),
            "summary": self.evidence.save(dest_id, f"run-{record['run_id']}.txt", run_summary(record)),
        }
        self.catalogue.record_run(dest_id, record)
        return record

    def certify_many(self, dest_ids: list[str], progress: Callable[[int], None] = lambda p: None) -> dict[str, Any]:
        """Certify several destinations. After N failures in a row on one connector we stop calling it (do not hammer it)."""
        started = self.clock()
        results, streak, skipped = [], {}, []
        for i, dest_id in enumerate(dest_ids, 1):
            key = self.catalogue.get(dest_id)["connector_key"]
            if streak.get(key, 0) >= self.max_failures:
                skipped.append({"destination_id": dest_id, "reason": f"{self.max_failures} failures in a row on connector {key!r}; stopped to avoid hammering it"})
                continue
            record = self.certify_destination(dest_id)
            streak[key] = 0 if record["verdict"] == "passed" else streak.get(key, 0) + 1
            results.append({"destination_id": dest_id, "verdict": record["verdict"], "run_id": record["run_id"], "duration_s": record["duration_s"],
                            "evidence": record["evidence"]["summary"]["download_url"]})
            progress(int(100 * i / len(dest_ids)))
        now = self.clock()
        verdicts = [r["verdict"] for r in results]
        return {"results": results, "skipped": skipped, "passed": verdicts.count("passed"), "failed": verdicts.count("failed"),
                "inconclusive": verdicts.count("inconclusive"),
                "manifest": build_manifest(kind="certification_batch", started_at=started, finished_at=now, settings={"simulation_mode": self.simulation})}

    def _unrunnable(self, row: dict[str, Any], exc: IncuBrixError) -> dict[str, Any]:
        now = self.clock()
        verdict = "inconclusive"  # we could not even try: that says nothing about the destination
        return {"run_id": f"skip-{uuid.uuid4().hex[:10]}", "destination_id": row["id"], "connector_key": row["connector_key"], "tier": None,
                "started_at": now, "finished_at": now, "duration_s": 0.0, "steps": [], "verdict": verdict, "warnings": [],
                "failure": {"kind": exc.code.value, "summary": f"{exc.code.value}: {exc.message}", "details": exc.details},
                "manifest": build_manifest(kind="certification", started_at=now, finished_at=now, settings={"simulation_mode": self.simulation})}


def register_certification_jobs(jobs, service: CertificationService, catalogue: Catalogue) -> None:
    def certify_job(ctx, params: dict[str, Any]) -> dict[str, Any]:
        if "destination_id" not in params:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "params.destination_id is required")
        ctx.progress(10)
        record = service.certify_destination(params["destination_id"])
        return {"run_id": record["run_id"], "verdict": record["verdict"], "failure": record.get("failure"), "evidence": record["evidence"], "manifest": record["manifest"]}

    def certify_batch_job(ctx, params: dict[str, Any]) -> dict[str, Any]:
        ids = params.get("destination_ids")
        if ids is None:
            ids = [r["id"] for r in catalogue.list(connector_key=params.get("connector_key"), status=params.get("status"), limit=1000)["items"]]
        return service.certify_many(ids, ctx.progress)

    jobs.register("certify", certify_job)
    jobs.register("certify_batch", certify_batch_job)
