"""Run the AI rules reader (Claude) over the real catalogue's rules pages:

    set the key in your own terminal first (never in a file): GEMINI_API_KEY (and RULES_MODEL, a Gemini Flash model id)
    or ANTHROPIC_API_KEY, then:
    python -m scripts.live_rules_capture [research_dir] [output_dir] [--extractor gemini|anthropic|mock] [--limit N]
    defaults: samples/research  catalogue/real/ai_rules  --extractor anthropic

For every researched community that has a rules page:
  1. the rules page is fetched (robots.txt respected, one polite request); if it cannot be read (blocked, empty, script-only),
     the verbatim quotes saved by the research step are used as the text instead, and the result says so (source: "stored quotes");
  2. the model reads that text and PROPOSES the five rules, each with a quote (app.rules_capture.AnthropicExtractor);
  3. every proposal is VERIFIED: the quote must really appear in the text, the value must have the right shape (app.rules_capture.verify);
  4. the result is saved as UNCONFIRMED: a person must confirm before any rule is used (these destinations are catalogue only anyway).
Output: results.json (one record per community, with tokens and cost), SUMMARY.md. scripts/build_real_catalogue.py then uses results.json.
The key is read from the environment only; it is never written to any file or log. Cost: the model's prices come from RULES_PRICE_PER_MTOK_IN / _OUT.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.errors import IncuBrixError
from app.rules_capture import AnthropicExtractor, GeminiExtractor, MockExtractor, html_to_text, verify

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "IncuBrix-Catalogue-Importer/0.1 (+catalogue research; respects robots.txt)"
MIN_PAGE_CHARS = 300


def load_entries(folder: Path) -> list[dict]:
    seen, items = set(), []
    for path in sorted(folder.glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8")):
            key = (entry.get("rules_url") or entry["url"]).rstrip("/").casefold()
            if key in seen:
                continue
            seen.add(key)
            entry["_source_file"] = path.name
            items.append(entry)
    return items


def robots_allows(http: httpx.Client, url: str) -> bool:
    parts = urlsplit(url)
    parser = urllib.robotparser.RobotFileParser()
    try:
        resp = http.get(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=10)
        if resp.status_code >= 400:
            return True  # no robots.txt: allowed
        parser.parse(resp.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except httpx.HTTPError:
        return True


def page_text_for(http: httpx.Client, entry: dict) -> tuple[str, str]:
    """(text, source) where source is 'rules page' or 'stored quotes' or '' when there is nothing to read."""
    url = entry.get("rules_url")
    if url:
        try:
            if robots_allows(http, url):
                resp = http.get(url, timeout=15, follow_redirects=True)
                if resp.status_code == 200:
                    text = html_to_text(resp.text)
                    if len(text) >= MIN_PAGE_CHARS:
                        return text, "rules page"
        except httpx.HTTPError:
            pass
    quotes = [q["quote"] for q in entry.get("rules_quotes", []) if q.get("quote")]
    if quotes:
        return "\n".join(quotes), "stored quotes"
    return "", ""


def process(entry: dict, http: httpx.Client, extractor) -> dict:
    record = {"name": entry["name"], "url": entry["url"], "rules_url": entry.get("rules_url"), "source": "", "rules": {}, "evidence": {},
              "dropped": [], "usage": {"input_tokens": 0, "output_tokens": 0}, "cost_usd": 0.0, "error": None}
    text, source = page_text_for(http, entry)
    record["source"] = source
    if not text:
        record["error"] = "no rules text available (no readable page and no stored quotes)"
        return record
    for attempt in range(4):
        try:
            found = extractor.extract(text, entry.get("rules_url"))
            break
        except IncuBrixError as exc:
            if exc.code.value == "PLATFORM_RATE_LIMIT" and attempt < 3:
                time.sleep(min(float((exc.details or {}).get("retry_after_s", 20)), 60))
                continue
            if exc.code.value == "PLATFORM_UNAVAILABLE" and attempt < 3 and "could not be reached" in exc.message:  # a timeout or dropped connection: asking again is safe (a read, not a post)
                time.sleep(5)
                continue
            record["error"] = f"{exc.code.value}: {exc.message}"
            return record
    rules, evidence, dropped = verify(found["rules"], text)
    record.update(rules=rules, evidence=evidence, dropped=dropped, usage=found["usage"])
    if hasattr(extractor, "cost_usd"):
        record["cost_usd"] = extractor.cost_usd(found["usage"])
    return record


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=")[0]: (a.split("=", 1)[1] if "=" in a else "1") for a in sys.argv[1:] if a.startswith("--")}
    research = Path(args[0]) if args else ROOT / "samples" / "research"
    out = Path(args[1]) if len(args) > 1 else ROOT / "catalogue" / "real" / "ai_rules"
    kind = flags.get("--extractor", "anthropic")
    limit = int(flags["--limit"]) if "--limit" in flags else None
    out.mkdir(parents=True, exist_ok=True)

    http = httpx.Client(headers={"User-Agent": USER_AGENT})
    price_in, price_out = float(os.environ.get("RULES_PRICE_PER_MTOK_IN", "0")), float(os.environ.get("RULES_PRICE_PER_MTOK_OUT", "0"))
    if kind == "mock":
        extractor = MockExtractor()
    elif kind == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if not key or not os.environ.get("RULES_MODEL"):
            raise SystemExit("Set GEMINI_API_KEY and RULES_MODEL (a Gemini Flash model id from Google AI Studio) in this terminal first. The key is read from the environment only.")
        extractor = GeminiExtractor(httpx.Client(), key, os.environ["RULES_MODEL"], price_in, price_out)
    else:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("Set ANTHROPIC_API_KEY in this terminal first (it is read from the environment only).")
        extractor = AnthropicExtractor(httpx.Client(), key, os.environ.get("RULES_MODEL", "claude-haiku-5-5"), price_in, price_out)

    entries = [e for e in load_entries(research) if e.get("rules_url") or e.get("rules_quotes")]
    if limit:
        entries = entries[:limit]

    # Resume: communities already read without an error (in an earlier run, by any model) are kept, so a stopped run loses nothing.
    cache_file = out / "progress.jsonl"
    done: dict[str, dict] = {}
    if cache_file.is_file():
        for line in cache_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["name"]] = rec
    todo = [e for e in entries if e["name"] not in done]
    print(f"{len(entries)} communities with a rules page or stored quotes; {len(done)} already done; {len(todo)} to read; extractor: {extractor.name}")

    state = {"last_call": 0.0, "rate_limited_in_a_row": 0, "stop": False}
    lock = threading.Lock()
    min_interval = float(os.environ.get("RULES_MIN_INTERVAL_S", "6"))  # keeps us under a free tier's calls per minute

    class Paced:
        name = extractor.name

        def extract(self, page_text, url):
            with lock:
                wait = state["last_call"] + min_interval - time.time()
                if wait > 0:
                    time.sleep(wait)
                state["last_call"] = time.time()
            return extractor.extract(page_text, url)

        def cost_usd(self, usage):
            return extractor.cost_usd(usage) if hasattr(extractor, "cost_usd") else 0.0

    paced = Paced()
    started = time.time()
    with ThreadPoolExecutor(max_workers=3) as pool:
        def run_one(entry: dict) -> dict:
            if state["stop"]:
                return {"name": entry["name"], "source": "", "rules": {}, "dropped": [], "usage": {"input_tokens": 0, "output_tokens": 0}, "cost_usd": 0.0, "error": "skipped: the free quota ran out (run the same command again later, or with another model, to continue)"}
            record = process(entry, http, paced)
            record["extractor"] = extractor.name
            if record["error"] and "PLATFORM_RATE_LIMIT" in record["error"]:
                state["rate_limited_in_a_row"] += 1
                if state["rate_limited_in_a_row"] >= 4:
                    state["stop"] = True
            else:
                state["rate_limited_in_a_row"] = 0
            if not record["error"]:
                with lock, cache_file.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"  {record['name'][:40]:40} {record['source'] or '-':14} verified rules: {len(record['rules'])}" + (f"  ERROR: {record['error'][:90]}" if record["error"] else ""), flush=True)
            return record

        fresh = list(pool.map(run_one, todo))

    records = [done[e["name"]] for e in entries if e["name"] in done] + [r for r in fresh if r["error"]] + [r for r in fresh if not r["error"]]
    names_used = sorted({r.get("extractor", extractor.name) for r in records if not r["error"]}) or [extractor.name]
    if state["stop"]:
        print("\nSTOPPED: the free quota is used up. Nothing is lost: run the same command again later (or set RULES_MODEL to another model) and it continues where it stopped.\n")
    (out / "results.json").write_text(json.dumps({"extractor": ", ".join(names_used), "records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    with_rules = [r for r in records if r["rules"]]
    proposals = sum(len(r["rules"]) + len(r["dropped"]) for r in records)
    dropped = sum(len(r["dropped"]) for r in records)
    tokens_in = sum(r["usage"]["input_tokens"] for r in records)
    tokens_out = sum(r["usage"]["output_tokens"] for r in records)
    errors = [r for r in records if r["error"]]
    by_source = {s: sum(1 for r in records if r["source"] == s) for s in ("rules page", "stored quotes", "")}
    lines = ["# AI rules capture on the real catalogue (unconfirmed proposals)", "",
             f"- extractor: `{extractor.name}`; communities read: {len(records)} (text from the live rules page: {by_source['rules page']}; from stored verbatim quotes: {by_source['stored quotes']}; nothing to read: {by_source['']})",
             f"- communities with at least one VERIFIED rule: **{len(with_rules)}**; verified rules: {sum(len(r['rules']) for r in records)}",
             f"- proposals dropped because the quote was not on the page or the value had the wrong shape: {dropped} of {proposals}",
             f"- calls that failed: {len(errors)}" + (f" ({'; '.join(sorted({r['error'][:60] for r in errors}))})" if errors else ""),
             f"- tokens: {tokens_in} in, {tokens_out} out; estimated cost: **${sum(r['cost_usd'] for r in records):.4f}** (prices from RULES_PRICE_PER_MTOK_IN/OUT); run time {time.time() - started:.0f} s", "",
             "Every rule above carries a quote that was checked as an exact substring of the text the model read. All proposals are UNCONFIRMED: a person must confirm them before use, and these destinations are catalogue only."]
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
