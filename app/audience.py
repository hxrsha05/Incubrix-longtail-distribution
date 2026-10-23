"""Relevance of a DESTINATION to IncuBrix's audience (Advanced level): a score from 1 to 5 with a short reason.

IncuBrix's audience is AI, video, marketing, SaaS, creators and founders. A destination is scored by how many of those six
segments its description touches (name, topics, site title and description, detected platform), and how strongly:
  no segment                      1     one segment, weak signal   2     one segment, strong signal   3
  two segments                    3     three segments             4     four or more segments        5
A signal is "strong" when three or more different words point at the same segment. The score is a transparent rule, the same
answer every time, and the reason names the segments and the words that decided it.
(This is not the content-to-destination fit in app/relevance.py, which asks "does THIS post suit THIS place".)
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

AUDIENCE: dict[str, set[str]] = {
    "AI": {"ai", "artificial", "machine-learning", "machinelearning", "ml", "llm", "llms", "gpt", "neural", "generative", "chatbot", "prompt", "model", "models", "deep-learning", "openai", "anthropic"},
    "video": {"video", "videos", "film", "filmmaking", "footage", "clip", "clips", "youtube", "editing", "editor", "vlog", "streaming", "movie", "cinema", "premiere", "davinci", "obs"},
    "marketing": {"marketing", "seo", "growth", "advertising", "ads", "audience", "branding", "content", "social-media", "newsletter", "copywriting", "campaign", "conversion", "analytics"},
    "SaaS": {"saas", "software", "startup", "startups", "b2b", "product", "api", "devtools", "developer", "developers", "subscription", "cloud", "platform", "no-code", "nocode", "programming"},
    "creators": {"creator", "creators", "influencer", "podcast", "podcasting", "streamer", "artist", "indie", "maker", "makers", "blogger", "blogging", "youtuber", "photography", "design"},
    "founders": {"founder", "founders", "entrepreneur", "entrepreneurs", "bootstrapped", "indie-hacker", "indiehackers", "solopreneur", "venture", "ycombinator", "startup", "startups", "business"},
}
WORD = re.compile(r"[a-z][a-z0-9+#-]{1,}")


class AudienceScore(BaseModel):
    score: int  # 1 to 5
    reason: str
    segments: dict[str, list[str]]  # segment -> the words that matched


def descriptor(row: dict[str, Any]) -> str:
    dest = row["destination"]
    extra = dest.get("extra") or {}
    parts = [dest.get("name", ""), " ".join(row.get("topics") or []), extra.get("site_title") or "", extra.get("site_description") or "",
             extra.get("platform_key") or "", " ".join(extra.get("audience_tags") or [])]
    return " ".join(parts).lower()


def score_audience(row: dict[str, Any]) -> AudienceScore:
    words = set(WORD.findall(descriptor(row)))
    # "open-source" style words also count by their parts
    words |= {p for w in list(words) for p in w.split("-") if len(p) > 1}
    hits = {segment: sorted(words & vocabulary) for segment, vocabulary in AUDIENCE.items()}
    matched = {s: w for s, w in hits.items() if w}
    n = len(matched)
    strong = any(len(w) >= 3 for w in matched.values())
    score = 1 if n == 0 else 3 if (n == 1 and strong) else 2 if n == 1 else 3 if n == 2 else 4 if n == 3 else 5
    if n == 0:
        reason = f"{score}/5: nothing in its description points at IncuBrix's audience (AI, video, marketing, SaaS, creators, founders)."
    else:
        shown = "; ".join(f"{s} ({', '.join(w[:3])})" for s, w in matched.items())
        missing = [s for s in AUDIENCE if s not in matched]
        reason = f"{score}/5: matches {n} of 6 audience segments: {shown}." + (f" No signal for {', '.join(missing)}." if missing and n < 4 else "")
    return AudienceScore(score=score, reason=reason, segments=matched)
