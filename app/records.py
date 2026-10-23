"""The destination record the catalogue tools must produce (brief Appendix A), and the destination levels.

name, url, platform_key, surface, category, audience_tags, language, geo, est_audience, rules_url, frequency_cap,
self_promo_allowed, link_policy, disclosure_required, automation_allowed, connector_key, relevance_score (1-5),
relevance_reason, level, last_certified_at

Levels:  L0 catalogued only (not certified, or no way to post)   L1 assisted (a person posts)   L2 bridged or feed
         L3 native API, publish + status + delete tested          L4 native plus metrics
A destination counts for a level only once it is CERTIFIED; until then it is L0 whatever its connector.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from app.audience import score_audience
from app.connectors.registry import ConnectorRegistry

RECORD_FIELDS = ["name", "url", "platform_key", "surface", "category", "audience_tags", "language", "geo", "est_audience", "rules_url", "frequency_cap",
                 "self_promo_allowed", "link_policy", "disclosure_required", "automation_allowed", "connector_key", "relevance_score", "relevance_reason",
                 "level", "last_certified_at"]
CATEGORY = {"discourse": "forum", "lemmy": "forum", "ghost": "blog", "hashnode": "blog", "devto": "blog", "kit": "newsletter", "feed": "video feed",
            "webhook": "partner website", "assisted": "community (posted by hand)"}


def level_of(row: dict[str, Any], registry: ConnectorRegistry) -> str:
    if row["status"] != "certified":
        return "L0"
    manifest = registry.manifests.get(row["connector_key"])
    if manifest is None:
        return "L0"
    if manifest.tier == "assisted":
        return "L1"
    if manifest.tier in ("bridge", "feed"):
        return "L2"
    return "L4" if "metrics" in manifest.capabilities else "L3"


def destination_record(row: dict[str, Any], registry: ConnectorRegistry) -> dict[str, Any]:
    dest, extra = row["destination"], row["destination"].get("extra") or {}
    rules = dest.get("rules") or {}
    manifest = registry.manifests.get(row["connector_key"])
    surface = dest.get("surface") or (manifest.surfaces[0].key if manifest and manifest.surfaces else None)
    audience = score_audience(row)
    return {
        "name": dest["name"],
        "url": dest.get("base_url") or extra.get("submit_url") or extra.get("site_url"),
        "platform_key": extra.get("platform_key") or row["connector_key"],
        "surface": surface,
        "category": extra.get("category") or CATEGORY.get(row["connector_key"], "other"),
        "audience_tags": row.get("topics") or [],
        "language": row.get("language"),
        "geo": extra.get("geo"),
        "est_audience": extra.get("member_count"),
        "rules_url": extra.get("rules_url"),
        "frequency_cap": rules.get("frequency_cap"),
        "self_promo_allowed": rules.get("self_promo_allowed"),
        "link_policy": rules.get("link_policy"),
        "disclosure_required": rules.get("disclosure_required"),
        "automation_allowed": rules.get("automation_allowed"),
        "connector_key": row["connector_key"],
        "relevance_score": audience.score,
        "relevance_reason": audience.reason,
        "level": level_of(row, registry),
        "last_certified_at": row.get("last_certified_at"),
    }


def records_to_csv(records: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=RECORD_FIELDS, lineterminator="\n")
    writer.writeheader()
    for r in records:
        flat = {k: ("; ".join(map(str, v)) if isinstance(v, list) else (f"{v['max_posts']} per {v['per_seconds']} s" if isinstance(v, dict) else v)) for k, v in r.items()}
        writer.writerow({k: ("" if v is None else v) for k, v in flat.items()})
    return out.getvalue()
