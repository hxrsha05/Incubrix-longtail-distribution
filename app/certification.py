"""Certification: proof that a destination really works, end to end, with the evidence saved.

One run does:  validate -> publish a clearly labelled test post -> confirm it is live -> status -> (metrics) ->
delete -> confirm it is gone.  Then it writes a run record.

Safety rules this module enforces:
  * the test post is ALWAYS cleaned up, also when a step fails, and within the time limit (default 10 minutes);
  * if publish itself failed with an unknown outcome (timeout, 5xx, or an unreadable answer) we look for the post with
    reconcile() and delete it, so a failure never leaves a stray post behind;
  * a 429 is waited out (up to a cap), never hammered;
  * a platform that changes its answer (a renamed field) fails certification and the destination is demoted.

Verdicts:  passed | failed (the connector or the destination is broken: demote) | inconclusive (rate limit, expired
token or outage: not proof of a broken connector, so the status becomes needs_attention instead of demoted).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable

from app.connectors.base import BaseConnector
from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, Destination, PublishResult, Rendition
from app.output_manifest import build_manifest

log = logging.getLogger("incubrix.certification")

INCONCLUSIVE_CODES = {ErrorCode.PLATFORM_RATE_LIMIT, ErrorCode.PLATFORM_UNAVAILABLE, ErrorCode.CONNECTION_AUTH_EXPIRED, ErrorCode.CONNECTOR_DISABLED}
CLEANUP_BACKOFF_S = (1, 2, 4, 8, 15, 30)


class CertificationFailed(Exception):
    """A step ran but its result was wrong (for example: still live after delete)."""


def make_test_rendition(run_id: str) -> Rendition:
    # long enough for the strictest platform rules we know (Discourse: title 15, body 20)
    return Rendition(
        title=f"IncuBrix certification test {run_id}",
        body="Automated IncuBrix certification post. It is deleted again within 10 minutes. Please ignore it.",
        tags=["certification"],
    )


def certify(
    connector: BaseConnector,
    destination: Destination,
    connection: Connection,
    *,
    destination_id: str,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    ttl_s: float = 600,
    rate_limit_wait_cap_s: float = 30,
    simulation: bool = True,
    run_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or uuid.uuid4().hex[:12]
    started = clock()
    record: dict[str, Any] = {
        "run_id": run_id, "destination_id": destination_id, "connector_key": connector.manifest.key,
        "tier": connector.manifest.tier, "target": connection.base_url or destination.base_url, "started_at": started, "steps": [], "verdict": "passed", "warnings": [],
    }
    state: dict[str, Any] = {"post": None, "published_at": None, "deleted": False}

    def step(name: str, fn: Callable[[], Any]) -> Any:
        t0 = clock()
        try:
            value = fn()
        except BaseException as exc:
            record["steps"].append({"name": name, "ok": False, "started_at": t0, "duration_s": round(clock() - t0, 3), "detail": {"error": _describe(exc)}})
            raise
        record["steps"].append({"name": name, "ok": True, "started_at": t0, "duration_s": round(clock() - t0, 3), "detail": value if isinstance(value, dict) else {}})
        return value

    try:
        if connector.manifest.tier == "assisted":
            _assisted_run(connector, destination, run_id, step)
        else:
            _full_run(connector, destination, connection, run_id, step, state, clock, sleep, rate_limit_wait_cap_s)
    except BaseException as exc:  # noqa: BLE001 - classify everything, then re-raise only what is not ours to judge
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        record["verdict"], record["failure"] = _classify(exc)
        log.warning("certification failed", extra={"destination": destination_id, "kind": record["failure"]["kind"]})
    finally:
        if state["post"] is None and connector.manifest.tier != "assisted" and record["verdict"] != "passed":
            _find_stray_post(connector, destination, connection, run_id, state, record)
        if state["post"] is not None and not state["deleted"]:
            _cleanup(connector, connection, state, record, clock, sleep, ttl_s)

    finished = clock()
    record["finished_at"], record["duration_s"] = finished, round(finished - started, 3)
    if state["published_at"] is not None:
        record["test_post"] = {"platform_post_id": state["post"].platform_post_id if state["post"] else None, "live_url": state["post"].live_url if state["post"] else None,
                               "deleted": state["deleted"], "deleted_after_s": round(state["deleted_at"] - state["published_at"], 3) if state.get("deleted_at") else None,
                               "limit_s": ttl_s}
        if not state["deleted"]:
            record["verdict"] = "failed"
            record["failure"] = record.get("failure") or {"kind": "CLEANUP_FAILED", "summary": "the test post could not be deleted in time; remove it by hand", "details": {}}
    record["manifest"] = build_manifest(
        kind="certification", started_at=started, finished_at=finished, connector_version=connector.manifest.version,
        settings={"simulation_mode": simulation, "test_post_ttl_s": ttl_s, "rate_limit_wait_cap_s": rate_limit_wait_cap_s},
        warnings=record["warnings"], ai_generated=False,
    )
    return record


# ---------------------------------------------------------------- the two kinds of run
def _full_run(connector, destination, connection, run_id, step, state, clock, sleep, wait_cap) -> None:
    rendition = make_test_rendition(run_id)
    expected = connector.effective_spec(destination).get("certify_status", "live")  # email connectors publish drafts, never sends
    verdict = step("validate", lambda: _validate(connector, rendition, destination, connection))
    if not verdict["ok"]:
        raise CertificationFailed(f"our own test post fails validation: {verdict['violations']}")

    def do_publish() -> dict[str, Any]:
        for attempt in (1, 2):
            try:
                post = connector.publish(rendition, destination, connection, f"cert-{run_id}")
            except IncuBrixError as exc:
                if exc.code is ErrorCode.PLATFORM_RATE_LIMIT and attempt == 1:  # a definite failure: waiting and retrying is safe
                    wait = min(float(exc.details.get("retry_after_s") or 5), wait_cap)
                    sleep(wait)
                    continue
                raise
            state["post"], state["published_at"] = post, clock()
            return {"platform_post_id": post.platform_post_id, "live_url": post.live_url, "status": post.status, "attempts": attempt}
        raise AssertionError("unreachable")

    step("publish", do_publish)
    post: PublishResult = state["post"]

    def confirm_live() -> dict[str, Any]:
        if post.status != expected or not post.live_url:
            raise CertificationFailed(f"publish did not report a {expected!r} post (status={post.status!r}, live_url={post.live_url!r})")
        return {"live_url": post.live_url, "expected_status": expected}

    step("confirm_live", confirm_live)

    def status_live() -> dict[str, Any]:
        s = connector.get_status(post.platform_post_id, connection)
        if s.status != expected:
            raise CertificationFailed(f"get_status says {s.status!r}, expected {expected!r}")
        return {"status": s.status, "live_url": s.live_url}

    step("status", status_live)

    if connector.manifest.supports("fetch_metrics"):
        step("metrics", lambda: connector.fetch_metrics(post.platform_post_id, connection, None).model_dump())

    def delete() -> dict[str, Any]:
        result = connector.delete(post.platform_post_id, connection)
        if not result.deleted:
            raise CertificationFailed("delete reported deleted=false")
        state["deleted"], state["deleted_at"] = True, clock()
        return {"deleted": True}

    step("delete", delete)

    def confirm_gone() -> dict[str, Any]:
        s = connector.get_status(post.platform_post_id, connection)
        if s.status == "live" or (expected != "live" and s.status != "not_found"):
            raise CertificationFailed(f"the post is still there after delete (status {s.status!r})")
        return {"status_after_delete": s.status, "note": "unpublished, not erased (platform has no delete)" if s.status == "draft" else ""}

    step("confirm_deleted", confirm_gone)


def _assisted_run(connector, destination, run_id, step) -> None:
    def build() -> dict[str, Any]:
        pack = connector.build_assist_pack(make_test_rendition(run_id), destination)
        if not (pack.caption and pack.deep_link and pack.checklist):
            raise CertificationFailed("assist pack is missing its caption, deep link or checklist")
        return {"deep_link": pack.deep_link, "checklist_items": len(pack.checklist), "caption_chars": len(pack.caption), "note": "assisted: nothing is posted; a person posts by hand"}

    step("build_assist_pack", build)


def _validate(connector, rendition, destination, connection) -> dict[str, Any]:
    result = connector.validate(rendition, destination, connection)
    return {"ok": result.ok, "violations": [v.model_dump() for v in result.violations]}


# ---------------------------------------------------------------- safety net
def _find_stray_post(connector, destination, connection, run_id, state, record) -> None:
    """Publish failed without giving us an id. The platform may still have made the post: look for it."""
    try:
        found = connector.reconcile(make_test_rendition(run_id), destination, connection, f"cert-{run_id}")
    except (NotImplementedError, IncuBrixError, KeyError, TypeError, ValueError) as exc:
        record["warnings"].append(f"could not check for a stray test post after the failure ({type(exc).__name__}); check the destination by hand")
        return
    if found is not None:
        state["post"], state["published_at"] = found, state["published_at"] or time.time()
        record["warnings"].append("publish failed but the platform had created the post; it was found by reconcile and will be deleted")


def _cleanup(connector, connection, state, record, clock, sleep, ttl_s) -> None:
    """Delete the test post, retrying with backoff until the time limit is nearly used up."""
    deadline = state["published_at"] + ttl_s
    attempts, t0 = 0, clock()
    post = state["post"]
    while True:
        attempts += 1
        try:
            if connector.delete(post.platform_post_id, connection).deleted:
                state["deleted"], state["deleted_at"] = True, clock()
                break
        except IncuBrixError as exc:
            record["warnings"].append(f"cleanup attempt {attempts} failed: {exc.code.value}")
        wait = CLEANUP_BACKOFF_S[min(attempts - 1, len(CLEANUP_BACKOFF_S) - 1)]
        if clock() + wait >= deadline:
            break
        sleep(wait)
    record["steps"].append({"name": "cleanup", "ok": state["deleted"], "started_at": t0, "duration_s": round(clock() - t0, 3), "detail": {"attempts": attempts}})


# ---------------------------------------------------------------- classification
def _describe(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, IncuBrixError):
        return {"code": exc.code.value, "message": exc.message, "details": exc.details}
    return {"type": type(exc).__name__, "message": str(exc)[:300]}


def _classify(exc: BaseException) -> tuple[str, dict[str, Any]]:
    if isinstance(exc, IncuBrixError):
        verdict = "inconclusive" if exc.code in INCONCLUSIVE_CODES else "failed"
        return verdict, {"kind": exc.code.value, "summary": f"{exc.code.value}: {exc.message}", "details": exc.details}
    if isinstance(exc, CertificationFailed):
        return "failed", {"kind": "CHECK_FAILED", "summary": str(exc), "details": {}}
    if isinstance(exc, KeyError):  # the platform's answer no longer has a field we rely on
        return "failed", {"kind": "RESPONSE_SHAPE_CHANGED", "summary": f"the platform's response changed: field {exc.args[0]!r} is missing", "details": {"missing_field": str(exc.args[0])}}
    if isinstance(exc, (TypeError, ValueError, AttributeError)):
        return "failed", {"kind": "RESPONSE_SHAPE_CHANGED", "summary": f"the platform's response could not be read: {type(exc).__name__}", "details": {}}
    return "failed", {"kind": "INTERNAL_ERROR", "summary": f"unexpected {type(exc).__name__}", "details": {}}


def run_summary(record: dict[str, Any]) -> str:
    """A short human-readable version of a run record, saved next to the JSON as evidence."""
    lines = [f"Certification run {record['run_id']}", f"destination: {record['destination_id']}  connector: {record['connector_key']}",
             f"verdict: {record['verdict'].upper()}   duration: {record.get('duration_s')} s"]
    for s in record["steps"]:
        lines.append(f"  [{'ok' if s['ok'] else 'FAIL'}] {s['name']} ({s['duration_s']} s)")
    if "failure" in record:
        lines.append(f"failure: {record['failure']['summary']}")
    if "test_post" in record:
        tp = record["test_post"]
        lines.append(f"test post: id={tp['platform_post_id']} url={tp['live_url']} deleted={tp['deleted']} after {tp['deleted_after_s']} s (limit {tp['limit_s']} s)")
    lines += [f"warning: {w}" for w in record["warnings"]]
    return "\n".join(lines) + "\n"
