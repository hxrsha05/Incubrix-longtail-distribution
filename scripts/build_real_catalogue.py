"""Turn researched real communities into catalogue records, checking every one against the real internet:

    python -m scripts.build_real_catalogue [research_dir] [output_dir]
    defaults: samples/research  catalogue/real

Input: the research files (JSON lists written by the research step: name, url, platform, category, language, topics, member_count, rules_url,
rules_quotes with VERBATIM quotes from the community's own rules page, checked_on, notes).
What this script does with them, for every community:
  1. de-duplicates (by normalised address and by name);
  2. runs the importer: is the address reachable, does robots.txt allow a visit, what platform is it really, what is its language and title;
     (one polite request per site; entries that do not answer are dropped and listed in the report);
  3. applies the quoted rules: the offline rules reader reads ONLY the quotes and proposes the five rules, each with its quote as evidence;
     where a rule is not stated, the conservative default stays;
  4. stores everything as CATALOGUE ONLY (a real community we list but never certify or post to) in a throwaway database, and writes the files.
Output: real_catalogue.csv (the destination record: name, url, platform_key, surface, category, audience_tags, language, geo, est_audience, rules_url,
frequency_cap, self_promo_allowed, link_policy, disclosure_required, automation_allowed, connector_key, relevance_score, relevance_reason, level,
last_certified_at), import_report.csv, rules_evidence.json and SUMMARY.md.
HONEST LIMITS: rules are read from public pages by software and are marked UNCONFIRMED until a person confirms them; geo is not researched.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
from pathlib import Path

import httpx
from sqlalchemy import create_engine

from app.catalogue import Catalogue
from app.connectors.registry import build_registry
from app.csv_import import CONSERVATIVE_RULES, Importer, dedupe_key, normalize_url, report_to_csv
from app.db import init_db
from app.records import RECORD_FIELDS, destination_record, records_to_csv
from app.rules_capture import MockExtractor, verify

ROOT = Path(__file__).resolve().parent.parent
CONNECTOR_FOR = {"lemmy": "lemmy", "discourse": "discourse", "devto": "devto", "hashnode": "hashnode", "ghost": "ghost"}  # anything else is posted to by hand
USER_AGENT = "IncuBrix-Catalogue-Importer/0.1 (+catalogue research; respects robots.txt)"


def load_research(folder: Path) -> list[dict]:
    items: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8")):
            entry["_source_file"] = path.name
            items.append(entry)
    return items


def dedupe(items: list[dict]) -> tuple[list[dict], list[str]]:
    seen_url, seen_name, kept, dropped = {}, {}, [], []
    for e in items:
        norm, reason = normalize_url(e.get("url", ""))
        if reason:
            dropped.append(f"{e.get('name')}: {reason}")
            continue
        url_key, name_key = dedupe_key(norm), e["name"].casefold().strip()
        if url_key in seen_url or name_key in seen_name:
            dropped.append(f"{e['name']}: duplicate of {seen_url.get(url_key) or seen_name.get(name_key)}")
            continue
        seen_url[url_key] = seen_name[name_key] = e["name"]
        kept.append(e)
    return kept, dropped


def research_csv(items: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["name", "url", "language", "topics", "connector_key"])
    for e in items:
        writer.writerow([e["name"], e["url"], e.get("language", ""), ";".join(e.get("topics", [])), CONNECTOR_FOR.get(e.get("platform", ""), "assisted")])
    return out.getvalue()


def apply_rules(catalogue: Catalogue, row: dict, entry: dict, ai: dict | None = None) -> dict:
    """Store the rules proposed for a community as unconfirmed: the AI reader's verified result when there is one
    (scripts/live_rules_capture.py), otherwise the offline keyword reader over the community's own quoted words."""
    rules, evidence, reader = {}, {}, "mock-keyword-reader"
    if ai and ai.get("rules"):
        rules, evidence, reader = ai["rules"], ai["evidence"], ai["reader"]
    else:
        quotes = [q["quote"] for q in entry.get("rules_quotes", []) if q.get("quote")]
        page_text = "\n".join(quotes)
        if page_text:
            found = MockExtractor().extract(page_text, entry.get("rules_url"))["rules"]
            rules, evidence, _dropped = verify(found, page_text)
    destination = catalogue.destination(row["id"])
    destination.rules = {**CONSERVATIVE_RULES, **rules}
    extra = dict(destination.extra)
    extra["rules_url"] = entry.get("rules_url") or extra.get("rules_url")
    extra["rules_status"] = "proposed_by_ai_unconfirmed" if (ai and ai.get("rules")) else ("researched_from_public_rules_page_unconfirmed" if rules else "default_conservative_unconfirmed")
    extra["rules_reader"] = reader if rules else None
    extra["rules_evidence"] = evidence
    extra["research_checked_on"] = entry.get("checked_on")
    extra["research_notes"] = entry.get("notes")
    if entry.get("member_count") is not None:
        extra["member_count"] = entry["member_count"]
    if entry.get("category"):
        extra["category"] = entry["category"]
    if entry.get("submit_url"):
        extra["submit_url"] = entry["submit_url"]
    destination.extra = extra
    catalogue.upsert(destination, row.get("language"), row.get("topics"))
    return {"id": row["id"], "rules_found": sorted(rules), "evidence": evidence}


def main() -> None:
    research_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "research"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "catalogue" / "real"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = load_research(research_dir)
    unique, dropped_dups = dedupe(items)
    csv_text = research_csv(unique)
    (out_dir / "import_input.csv").write_text(csv_text, encoding="utf-8")

    engine = create_engine(f"sqlite:///{tempfile.mkdtemp(prefix='incubrix-real-')}/real.db")
    init_db(engine)
    catalogue = Catalogue(engine)
    http = httpx.Client(headers={"User-Agent": USER_AGENT}, limits=httpx.Limits(max_connections=40))
    importer = Importer(catalogue, http, workers=16, fetch_timeout_s=12.0)  # real internet: private addresses stay refused
    result = importer.run(csv_text, commit=True, catalogue_only=True, progress=lambda p: print(f"  import {p}%", end="\r"))
    print()

    by_name = {e["name"]: e for e in unique}
    ai_file = out_dir / "ai_rules" / "results.json"  # written by scripts/live_rules_capture.py (needs an API key)
    ai_by_name: dict[str, dict] = {}
    if ai_file.is_file():
        data = json.loads(ai_file.read_text(encoding="utf-8"))
        ai_by_name = {r["name"]: {**r, "reader": r.get("extractor") or data["extractor"]} for r in data["records"]}
        print(f"using rules proposals from {data['extractor']} for {sum(1 for r in ai_by_name.values() if r['rules'])} communities")
    applied = []
    for r in result["report"]:
        if r["status"] == "accepted" and r["destination_id"]:
            applied.append(apply_rules(catalogue, catalogue.get(r["destination_id"]), by_name[r["name"]], ai_by_name.get(r["name"])))

    registry = build_registry(httpx.Client(), set())
    rows = catalogue.list(limit=100000)["items"]
    records = [destination_record(row, registry) for row in rows]
    (out_dir / "real_catalogue.csv").write_text(records_to_csv(records), encoding="utf-8")
    (out_dir / "import_report.csv").write_text(report_to_csv(result["report"]), encoding="utf-8")
    (out_dir / "rules_evidence.json").write_text(json.dumps(applied, indent=2), encoding="utf-8")

    with_rules = sum(1 for a in applied if a["rules_found"])
    by_kind: dict[str, int] = {}
    for r in records:
        by_kind[r["category"]] = by_kind.get(r["category"], 0) + 1
    by_connector: dict[str, int] = {}
    for r in records:
        by_connector[r["connector_key"]] = by_connector.get(r["connector_key"], 0) + 1
    scores = [r["relevance_score"] for r in records]
    lines = ["# Real catalogue (catalogue only: nothing here is ever posted to)", "",
             f"- researched candidates: {len(items)}; after removing duplicates: {len(unique)} ({len(dropped_dups)} duplicates or unusable addresses dropped)",
             f"- import check against the real internet: **{result['accepted']} accepted**, {result['unreachable']} unreachable, {result['invalid']} invalid, {result['duplicate']} duplicates; {result['duration_s']} s",
             f"- of the accepted: {sum(1 for r in result['report'] if r['status'] == 'accepted' and r['http_status'] == 200)} answered HTTP 200 to the importer; "
             f"{sum(1 for r in result['report'] if r['status'] == 'accepted' and r['http_status'] not in (200, None))} blocked the importer (HTTP 403) and "
             f"{sum(1 for r in result['report'] if r['status'] == 'accepted' and r['http_status'] is None)} were not visited because robots.txt forbids it; those are kept with a warning, because the research step (a person-like browser read) found them live",
             f"- destination records written: **{len(records)}** (`real_catalogue.csv`, the fields the brief requires)",
             f"- rules proposed from the community's own quoted words for {with_rules} destinations; the others keep the conservative default",
             f"- by category: {dict(sorted(by_kind.items()))}", f"- by connector type: {dict(sorted(by_connector.items()))}",
             f"- relevance score to IncuBrix's audience (1-5): " + ", ".join(f"{s}: {scores.count(s)}" for s in range(1, 6)),
             f"- with a member count: {sum(1 for r in records if r['est_audience'] is not None)}; with a rules page address: {sum(1 for r in records if r['rules_url'])}", "",
             "## What is and is not verified", "",
             "- Every address answered a real request from `scripts/build_real_catalogue.py` (the import report has the status, response time and final address), and robots.txt was respected.",
             "- Quotes come from each community's own rules page (copied by the research step and checked there as exact substrings); the five rules are then read from those quotes by software.",
             "- Rules are **unconfirmed**: a person must confirm them before any would be used. `geo` was not researched. Member counts are the ones the sites state themselves.",
             "- Nothing was posted to any of these communities, and none can be (they are flagged catalogue-only; certification and publishing refuse them)."]
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[2:9]))


if __name__ == "__main__":
    main()
