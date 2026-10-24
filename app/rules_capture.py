"""AI rules capture (Advanced level): read a community's rules page, PROPOSE rules, and let a PERSON confirm them.

The safety design, in order:
  1. An extractor (an AI model, or the offline mock) reads the page text and proposes values for the five rules,
     each with a QUOTE from the page as evidence.
  2. Every proposal is VERIFIED: the rule must be a known one, the value must have the right shape, and the quote must
     really appear in the page text. Anything else is dropped and listed (this is what stops an invented rule).
  3. The result is a PENDING proposal with a diff against the destination's current rules. Nothing is applied.
  4. A person confirms or rejects it, with their name. Making a destination LOOSER (allowing more) needs an explicit
     acknowledgement on top. Only a confirmation changes the destination, and it records who and when.
Until then the destination keeps its conservative rules, so the system stays safe if nobody ever reviews.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
import uuid
from typing import Any, Callable, Protocol

import httpx
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from app.catalogue import Catalogue
from app.db import rule_proposals as t
from app.errors import ErrorCode, IncuBrixError
from app.output_manifest import build_manifest

RULE_KEYS = ("frequency_cap", "self_promo_allowed", "link_policy", "disclosure_required", "automation_allowed")
PERIOD_SECONDS = {"day": 86400, "week": 604800, "month": 2592000}
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "once": 1, "single": 1, "two": 2, "twice": 2, "three": 3, "four": 4, "five": 5}
MAX_PAGE_CHARS = 60_000


# ---------------------------------------------------------------- page text
def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|li|h[1-6]|div|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()[:MAX_PAGE_CHARS]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|•|^\s*[-*]\s+", text)
    return [p.strip(" -*•\t") for p in parts if p and len(p.strip()) > 8]


# ---------------------------------------------------------------- extractors
class Extractor(Protocol):
    name: str

    def extract(self, page_text: str, url: str | None) -> dict[str, Any]:
        """Return {"rules": [{"rule", "value", "quote", "confidence"}], "usage": {"input_tokens", "output_tokens"}}."""


class MockExtractor:
    """A deterministic keyword reader. It stands in for the AI model offline, in tests and in simulation mode."""

    name = "mock-keyword-reader"

    def extract(self, page_text: str, url: str | None) -> dict[str, Any]:
        found: list[dict[str, Any]] = []
        for sentence in sentences(page_text):
            low = sentence.lower()

            def add(rule: str, value: Any, confidence: float = 0.8) -> None:
                if not any(f["rule"] == rule for f in found):  # the first sentence that states a rule wins
                    found.append({"rule": rule, "value": value, "quote": sentence, "confidence": confidence})

            m = re.search(r"\b(\d+|a|an|one|once|single|two|twice|three|four|five)\b\s+(?:posts?|submissions?|threads?|topics?)?\s*(?:per|a|each|every|/)\s*(day|week|month)\b", low)
            if m:
                n = int(m.group(1)) if m.group(1).isdigit() else NUMBER_WORDS[m.group(1)]
                add("frequency_cap", {"max_posts": n, "per_seconds": PERIOD_SECONDS[m.group(2)]})
            if re.search(r"self[- ]promotion (is )?(not allowed|forbidden|prohibited|banned)|no self[- ]promotion|do not promote your own|no (advertising|ads|spam)|promotional (posts|content) (is|are) (not allowed|forbidden)", low):
                add("self_promo_allowed", False, 0.9)
            elif re.search(r"self[- ]promotion (is )?(welcome|allowed|ok|fine|permitted)", low):
                add("self_promo_allowed", True, 0.85)
            if re.search(r"no links|do not (post|include) (any )?links|links (are|is) not allowed|link posts are not allowed", low):
                add("link_policy", "none", 0.85)
            else:
                lm = re.search(r"(?:max(?:imum)?(?: of)?|at most|up to|only|no more than)\s+(\d+|a|one|a single|two|three)\s+links?\b|\b(a single|one) link\b", low)
                if lm:
                    word = next(g for g in lm.groups() if g)
                    word = word.replace("a single", "single")
                    add("link_policy", f"max:{int(word) if word.isdigit() else NUMBER_WORDS[word]}", 0.8)
            if re.search(r"(ai|artificial intelligence|machine)[- ]generated.*(must|should|needs? to|has to).*(disclos|label|mark|flag|tag)|(disclose|label|mark).*(ai|artificial intelligence)[- ]generated", low):
                add("disclosure_required", True, 0.85)
            if re.search(r"no bots|bots? (are|is) not allowed|automated (posting|posts|accounts|submissions?) (is|are) (not allowed|forbidden|prohibited|banned)|do not use bots", low):
                add("automation_allowed", False, 0.9)
        return {"rules": found, "usage": {"input_tokens": 0, "output_tokens": 0}}


SYSTEM_PROMPT = (
    "You read the rules page of an online community and report ONLY what the page states. "
    "Reply with one JSON object and nothing else: {\"rules\": [{\"rule\": <name>, \"value\": <value>, \"quote\": <exact text copied from the page>, \"confidence\": <0 to 1>}]}. "
    "Allowed rule names and values: frequency_cap = {\"max_posts\": int, \"per_seconds\": int} (day=86400, week=604800, month=2592000); "
    "self_promo_allowed = true or false; link_policy = \"allowed\" or \"none\" or \"max:N\"; disclosure_required = true or false (AI-generated content must be disclosed); "
    "automation_allowed = true or false (bots or automated posting). "
    "Include a rule only when the page clearly states it, and copy the quote exactly. If the page says nothing about a rule, leave it out. Never guess."
)


class AnthropicExtractor:
    """Claude through the Messages API. Needs ANTHROPIC_API_KEY. NOT verified against the live API from this laptop."""

    def __init__(self, http: httpx.Client, api_key: str, model: str, price_in: float = 0.0, price_out: float = 0.0, base_url: str = "https://api.anthropic.com") -> None:
        self.http, self.api_key, self.model, self.base_url = http, api_key, model, base_url.rstrip("/")
        self.price_in, self.price_out = price_in, price_out
        self.name = f"anthropic:{model}"

    def extract(self, page_text: str, url: str | None) -> dict[str, Any]:
        body = {"model": self.model, "max_tokens": 1500, "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": f"Rules page{f' ({url})' if url else ''}:\n\n{page_text}"}]}
        try:
            resp = self.http.post(f"{self.base_url}/v1/messages", json=body, timeout=60,
                                  headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        except httpx.HTTPError as exc:
            raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"the AI model could not be reached ({type(exc).__name__})", {"outcome_unknown": False}) from None
        if resp.status_code == 429:
            raise IncuBrixError(ErrorCode.PLATFORM_RATE_LIMIT, "the AI model is rate limiting us", {"retry_after_s": float(resp.headers.get("retry-after", 30))})
        if resp.status_code in (401, 403):
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "the AI model rejected the API key")
        if resp.status_code >= 400:
            raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"the AI model answered HTTP {resp.status_code}", {"http_status": resp.status_code})
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        match = re.search(r"\{.*\}", text, re.S)
        try:
            rules = json.loads(match.group(0))["rules"] if match else []
        except (ValueError, KeyError, TypeError):
            raise IncuBrixError(ErrorCode.PLATFORM_CONTENT_REJECTED, "the AI model did not return the JSON we asked for; nothing was proposed") from None
        usage = data.get("usage", {})
        return {"rules": rules if isinstance(rules, list) else [], "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}}

    def cost_usd(self, usage: dict[str, int]) -> float:
        return round(usage["input_tokens"] * self.price_in / 1e6 + usage["output_tokens"] * self.price_out / 1e6, 6)


class GeminiExtractor:
    """Google Gemini through the Generative Language REST API (the free tier of Google AI Studio is enough for this job).

    Same job and same output shape as AnthropicExtractor, so the verification step treats every reader the same way.
    Only public rules text is sent. The key comes from GEMINI_API_KEY and is sent in a header, never in the address."""

    def __init__(self, http: httpx.Client, api_key: str, model: str, price_in: float = 0.0, price_out: float = 0.0, base_url: str = "https://generativelanguage.googleapis.com") -> None:
        self.http, self.api_key, self.model, self.base_url = http, api_key, model, base_url.rstrip("/")
        self.price_in, self.price_out = price_in, price_out
        self.name = f"gemini:{model}"

    def extract(self, page_text: str, url: str | None) -> dict[str, Any]:
        body = {"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": f"Rules page{f' ({url})' if url else ''}:\n\n{page_text}"}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 8192}}
        try:
            resp = self.http.post(f"{self.base_url}/v1beta/models/{self.model}:generateContent", json=body, timeout=240,
                                  headers={"x-goog-api-key": self.api_key, "content-type": "application/json"})
        except httpx.HTTPError as exc:
            raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"the AI model could not be reached ({type(exc).__name__})", {"outcome_unknown": False}) from None
        if resp.status_code == 429:
            try:
                detail = str(resp.json().get("error", {}).get("message", ""))[:160]
            except ValueError:
                detail = ""
            raise IncuBrixError(ErrorCode.PLATFORM_RATE_LIMIT, f"the AI model is rate limiting us (free tier limits): {detail}", {"retry_after_s": float(resp.headers.get("retry-after", 30))})
        if resp.status_code in (401, 403) or (resp.status_code == 400 and "API key" in resp.text[:400]):
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "the AI model rejected the API key")
        if resp.status_code >= 400:
            raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"the AI model answered HTTP {resp.status_code}", {"http_status": resp.status_code})
        data = resp.json()
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        match = re.search(r"\{.*\}", text, re.S)
        try:
            rules = json.loads(match.group(0))["rules"] if match else []
        except (ValueError, KeyError, TypeError):
            raise IncuBrixError(ErrorCode.PLATFORM_CONTENT_REJECTED, "the AI model did not return the JSON we asked for; nothing was proposed") from None
        usage = data.get("usageMetadata", {})
        return {"rules": rules if isinstance(rules, list) else [], "usage": {"input_tokens": usage.get("promptTokenCount", 0), "output_tokens": usage.get("candidatesTokenCount", 0)}}

    def cost_usd(self, usage: dict[str, int]) -> float:
        return round(usage["input_tokens"] * self.price_in / 1e6 + usage["output_tokens"] * self.price_out / 1e6, 6)


# ---------------------------------------------------------------- verification and diff
def _valid_value(rule: str, value: Any) -> bool:
    if rule == "frequency_cap":
        return value is None or (isinstance(value, dict) and isinstance(value.get("max_posts"), int) and isinstance(value.get("per_seconds"), int)
                                 and value["max_posts"] >= 1 and value["per_seconds"] >= 60)
    if rule == "link_policy":
        return isinstance(value, str) and re.fullmatch(r"allowed|none|max:\d+", value) is not None
    return isinstance(value, bool)  # the three yes/no rules


def verify(proposed: list[dict[str, Any]], page_text: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Keep only well-formed proposals whose quote really is on the page. Returns (rules, evidence, dropped)."""
    page = _norm(page_text)
    rules: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    dropped: list[dict[str, Any]] = []
    for item in proposed:
        if not isinstance(item, dict):
            dropped.append({"item": str(item)[:100], "why": "not an object"})
            continue
        rule, value, quote = item.get("rule"), item.get("value"), str(item.get("quote") or "")
        if rule not in RULE_KEYS:
            dropped.append({"rule": str(rule), "why": "unknown rule"})
        elif not _valid_value(rule, value):
            dropped.append({"rule": rule, "why": f"value {value!r} has the wrong shape"})
        elif len(_norm(quote)) < 8 or _norm(quote) not in page:
            dropped.append({"rule": rule, "why": "the quote does not appear on the page (possible invention)", "quote": quote[:200]})
        elif rule in rules:
            dropped.append({"rule": rule, "why": "stated twice; the first was kept"})
        else:
            confidence = item.get("confidence")
            rules[rule] = value
            evidence[rule] = {"quote": quote, "confidence": float(confidence) if isinstance(confidence, (int, float)) else None}
    return rules, evidence, dropped


def _loosens(rule: str, old: Any, new: Any) -> bool:
    """True when `new` lets more through than `old`."""
    if old == new:
        return False
    if rule == "self_promo_allowed" or rule == "automation_allowed":
        return bool(new) and not old
    if rule == "disclosure_required":
        return (not new) and bool(old)
    if rule == "link_policy":
        rank = lambda v: 0 if v == "none" else 10**9 if v == "allowed" else int(str(v).split(":")[1])  # noqa: E731 - how many links get through
        return rank(new) > rank(old)
    if rule == "frequency_cap":
        if old is None:
            return False
        if new is None:
            return True
        return new["max_posts"] / new["per_seconds"] > old["max_posts"] / old["per_seconds"]
    return False


def make_diff(current: dict[str, Any], proposed: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"rule": r, "from": current.get(r), "to": v, "change": "same" if current.get(r) == v else "loosens" if _loosens(r, current.get(r), v) else "tightens"}
            for r, v in proposed.items()]


# ---------------------------------------------------------------- the service
class RulesCapture:
    def __init__(self, engine: Engine, catalogue: Catalogue, extractor: Extractor, http: httpx.Client, *, allow_private_hosts: bool = False, private_host_allowlist: frozenset[str] = frozenset(),
                 clock: Callable[[], float] = time.time) -> None:
        self.engine, self.catalogue, self.extractor, self.http = engine, catalogue, extractor, http
        self.allow_private_hosts, self.clock = allow_private_hosts, clock
        self.private_host_allowlist = private_host_allowlist

    def capture(self, dest_id: str, *, url: str | None = None, text: str | None = None) -> dict[str, Any]:
        row = self.catalogue.get(dest_id)
        if not url and not text:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "give the address of the rules page (url) or its text (text)")
        started = self.clock()
        page_text = html_to_text(text) if text else self._fetch(url)
        if len(page_text) < 20:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "the rules page has no readable text")
        result = self.extractor.extract(page_text, url)
        rules, evidence, dropped = verify(result["rules"], page_text)
        current = row["destination"].get("rules") or {}
        diff = make_diff(current, rules)
        usage = result.get("usage") or {"input_tokens": 0, "output_tokens": 0}
        cost = {"extractor": self.extractor.name, **usage, "usd": self.extractor.cost_usd(usage) if hasattr(self.extractor, "cost_usd") else 0.0}
        proposal_id = uuid.uuid4().hex[:16]
        with self.engine.begin() as conn:
            conn.execute(insert(t).values(
                id=proposal_id, destination_id=dest_id, status="pending", source_url=url, source_hash=hashlib.sha256(page_text.encode()).hexdigest(),
                extractor=self.extractor.name, proposed={"rules": rules, "evidence": evidence, "dropped": dropped}, diff=diff, cost=cost, created_at=started))
        return self.get(proposal_id) | {"manifest": build_manifest(
            kind="rules_capture", started_at=started, finished_at=self.clock(), settings={"extractor": self.extractor.name}, ai_generated=self.extractor.name != MockExtractor.name,
            models=[{"provider": self.extractor.name.split(":")[0], "model": self.extractor.name.split(":", 1)[-1], "role": "reads the rules page and proposes rules; a person confirms"}],
            model_calls=0 if self.extractor.name == MockExtractor.name else 1, cost_usd=cost["usd"],
            warnings=[f"{d.get('rule')}: {d['why']}" for d in dropped])}

    def get(self, proposal_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = conn.execute(select(t).where(t.c.id == proposal_id)).mappings().first()
        if row is None:
            raise IncuBrixError(ErrorCode.NOT_FOUND, f"no rule proposal {proposal_id!r}")
        return {"id": row["id"], "destination_id": row["destination_id"], "status": row["status"], "source_url": row["source_url"], "extractor": row["extractor"],
                "proposed": row["proposed"], "diff": row["diff"], "cost": row["cost"], "created_at": row["created_at"], "decided_at": row["decided_at"],
                "decided_by": row["decided_by"], "final_rules": row["final_rules"], "note": row["note"]}

    def list(self, dest_id: str) -> list[dict[str, Any]]:
        self.catalogue.get(dest_id)
        with self.engine.begin() as conn:
            ids = [r[0] for r in conn.execute(select(t.c.id).where(t.c.destination_id == dest_id).order_by(t.c.created_at.desc()))]
        return [self.get(i) for i in ids]

    def confirm(self, proposal_id: str, reviewer: str, *, overrides: dict[str, Any] | None = None, acknowledge_loosening: bool = False, note: str | None = None) -> dict[str, Any]:
        """A person applies the proposal (optionally with their own edits). This is the ONLY place rules change."""
        reviewer = (reviewer or "").strip()
        if not reviewer:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "reviewer (the name of the person confirming) is required")
        proposal = self.get(proposal_id)
        if proposal["status"] != "pending":
            raise IncuBrixError(ErrorCode.IDEMPOTENCY_KEY_REUSED, f"this proposal was already {proposal['status']}", {"status": proposal["status"]})
        chosen = {**proposal["proposed"]["rules"], **(overrides or {})}
        bad = [r for r, v in chosen.items() if r not in RULE_KEYS or not _valid_value(r, v)]
        if bad:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"invalid rule values: {bad}", {"rules": bad})
        row = self.catalogue.get(proposal["destination_id"])
        current = row["destination"].get("rules") or {}
        diff = make_diff(current, chosen)
        loosening = [d["rule"] for d in diff if d["change"] == "loosens"]
        if loosening and not acknowledge_loosening:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"this would make the destination LESS strict ({loosening}); confirm again with acknowledge_loosening=true if that is intended", {"loosening": loosening, "diff": diff})
        destination = self.catalogue.destination(proposal["destination_id"])
        destination.rules = {**current, **chosen}
        destination.extra = {**destination.extra, "rules_status": f"confirmed_by:{reviewer}", "rules_confirmed_at": self.clock(), "rules_proposal": proposal_id}
        self.catalogue.upsert(destination, row.get("language"), row.get("topics"))
        with self.engine.begin() as conn:
            conn.execute(update(t).where(t.c.id == proposal_id).values(status="confirmed", decided_at=self.clock(), decided_by=reviewer, final_rules=chosen, diff=diff, note=note))
        return self.get(proposal_id)

    def reject(self, proposal_id: str, reviewer: str, note: str | None = None) -> dict[str, Any]:
        if not (reviewer or "").strip():
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "reviewer is required")
        if self.get(proposal_id)["status"] != "pending":
            raise IncuBrixError(ErrorCode.IDEMPOTENCY_KEY_REUSED, "this proposal was already decided")
        with self.engine.begin() as conn:
            conn.execute(update(t).where(t.c.id == proposal_id).values(status="rejected", decided_at=self.clock(), decided_by=reviewer.strip(), note=note))
        return self.get(proposal_id)

    # ---------- fetching the rules page ----------
    def _fetch(self, url: str) -> str:
        from urllib.parse import urlsplit

        from app.csv_import import host_is_blocked, normalize_url

        normalized, reason = normalize_url(url, self.allow_private_hosts or bool(self.private_host_allowlist))
        if reason:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"cannot use that address: {reason}")
        if host_is_blocked(urlsplit(normalized).hostname or "", self.allow_private_hosts, self.private_host_allowlist):
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "the service will not fetch private or local addresses")
        try:
            resp = self.http.get(normalized, timeout=10, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"could not fetch the rules page ({type(exc).__name__})") from None
        if resp.status_code >= 400:
            raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"the rules page answered HTTP {resp.status_code}", {"http_status": resp.status_code})
        return html_to_text(resp.text)
