"""Posting-rules engine (must-have 4): checked before EVERY post.

Pure function: no database, no clock, no network. Everything it needs is passed in, so it is easy to test.

Rules live on the destination (destination.rules). All five keys are REQUIRED - if one is missing the post is
blocked ("fail closed"): not knowing the rules is never a reason to post.

    frequency_cap:       {"max_posts": 1, "per_seconds": 604800}  or  null (no cap)
    self_promo_allowed:  true | false
    link_policy:         "allowed" | "none" | "max:N"
    disclosure_required: true | false      (AI-content disclosure)
    automation_allowed:  true | false      (false = a person must post; use the assisted connector)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.models import Rendition

REQUIRED_RULE_KEYS = ("frequency_cap", "self_promo_allowed", "link_policy", "disclosure_required", "automation_allowed")
_URL = re.compile(r"https?://[^\s)\]>\"']+", re.I)


@dataclass
class RuleViolation:
    rule: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "reason": self.reason}


@dataclass
class RuleDecision:
    action: str  # allow | block | reschedule
    violations: list[RuleViolation] = field(default_factory=list)
    reschedule_at: float | None = None  # epoch seconds, only for action == "reschedule"

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


_BARE = re.compile(r"(?<![\w@/.-])(?:www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|ai|co|dev|app|me|tv|xyz|info|gg|page|link)\b)(?:/[^\s)\]>\"']*)?", re.I)
_MARKDOWN_OR_HTML_LINK = re.compile(r"\]\(\s*([^)\s]+)\s*\)|href\s*=\s*[\"']([^\"']+)[\"']", re.I)

# strong signs of an advert in the text itself, for a post that was not marked promotional by its sender
PROMO_MARKERS = ("buy now", "% off", "discount code", "promo code", "use code", "limited time offer", "limited-time offer", "sign up today", "sign up now",
                 "free trial", "special offer", "order now", "shop now", "subscribe now", "act now", "check out our", "our new product", "now available at", "get yours")


def count_links(rendition: Rendition) -> int:
    """Every link in the post: full addresses, www. addresses, bare domains such as example.com/page, markdown and HTML links, and the
    link and canonical fields. Counting more rather than fewer is deliberate: a link rule that misses a link is the dangerous kind of mistake."""
    seen: set[str] = set()

    def norm(url: str) -> str:
        return re.sub(r"^https?://", "", url.strip().lower()).removeprefix("www.").rstrip("/.,;:!?")

    text = rendition.body
    for found in _URL.findall(text):
        seen.add(norm(found))
    for md, html_ in _MARKDOWN_OR_HTML_LINK.findall(text):
        seen.add(norm(md or html_))
    for found in _BARE.findall(text):
        seen.add(norm(found))
    for extra in (rendition.link_url, rendition.canonical_url):
        if extra:
            seen.add(norm(extra))
    return len({u for u in seen if u})


def promo_markers(rendition: Rendition) -> list[str]:
    text = f"{rendition.title} {rendition.body}".lower()
    return [m for m in PROMO_MARKERS if m in text]


def check_rules(
    rendition: Rendition,
    rules: dict[str, Any],
    *,
    now: float,
    automated: bool,
    post_times_since: Callable[[float], list[float]],
) -> RuleDecision:
    missing = [k for k in REQUIRED_RULE_KEYS if k not in rules]
    if missing:
        return RuleDecision("block", [RuleViolation("rules_missing", f"destination rules incomplete, missing: {missing}")])

    violations: list[RuleViolation] = []

    if automated and rules["automation_allowed"] is False:
        violations.append(RuleViolation("automation", "automated posting is not allowed here; use the assisted pack"))

    if rules["self_promo_allowed"] is False:
        if rendition.promotional:
            violations.append(RuleViolation("self_promotion", "this destination bans self-promotional posts"))
        elif len(markers := promo_markers(rendition)) >= 2:  # not marked promotional, but the text reads like an advert
            violations.append(RuleViolation("self_promotion", f"this destination bans self-promotional posts and the text looks promotional (matched: {', '.join(markers[:3])})"))

    policy = rules["link_policy"]
    links = count_links(rendition)
    if policy == "none" and links > 0:
        violations.append(RuleViolation("link_policy", f"links are not allowed here (found {links})"))
    elif isinstance(policy, str) and policy.startswith("max:"):
        limit = int(policy.split(":", 1)[1])
        if links > limit:
            violations.append(RuleViolation("link_policy", f"at most {limit} link(s) allowed (found {links})"))
    elif policy not in ("allowed", "none") and not (isinstance(policy, str) and policy.startswith("max:")):
        violations.append(RuleViolation("rules_invalid", f"unknown link_policy {policy!r}"))

    if rules["disclosure_required"] is True and rendition.ai_generated:
        if not rendition.ai_disclosure or rendition.ai_disclosure not in rendition.body:
            violations.append(RuleViolation("ai_disclosure", "AI-generated content needs a disclosure line in the post body"))

    if violations:  # a hard violation beats a reschedule: waiting would not fix it
        return RuleDecision("block", violations)

    cap = rules["frequency_cap"]
    if cap:
        window = float(cap["per_seconds"])
        recent = sorted(post_times_since(now - window))
        if len(recent) >= int(cap["max_posts"]):
            # the next slot opens when enough old posts have aged out of the window
            frees_at = recent[len(recent) - int(cap["max_posts"])] + window
            reason = f"frequency cap reached: {cap['max_posts']} post(s) per {int(window)}s"
            return RuleDecision("reschedule", [RuleViolation("frequency_cap", reason)], reschedule_at=frees_at)

    return RuleDecision("allow")
