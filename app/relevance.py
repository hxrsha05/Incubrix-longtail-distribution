"""Relevance score 1 to 5 with a short explanation: how well does this content fit this destination?

It is a transparent rule-based score (no model, no cost, same answer every time) built from four signals:
  topic     how much the content's words overlap the destination's topics, name and site description     0 to 3 points
  language  same language as the destination                                                             0 to 1 point
  fit       the content is allowed there: promotion vs the no-promo rule, and the platform accepts the type 0 to 1 point (or a veto)
  health    the destination has passed certification                                                      0 to 0.5 point
A destination with no topical overlap at all is capped at 2. A destination that would refuse the content outright (for example promotion to a no-promo community) is capped at 1,
whatever the topic says. The explanation names the signals so a person can see why.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from app.models import Rendition

STOPWORDS = set("""a an and are as at be but by for from has have how i in is it its of on or our that the their this to was we were what when
which who why will with you your about into than then them they these those can not new more most just also all any one two""".split())
WORD = re.compile(r"[a-z][a-z0-9+#-]{2,}")


class Relevance(BaseModel):
    destination_id: str
    score: int  # 1 to 5
    explanation: str
    signals: dict[str, Any]


def keywords(text: str) -> set[str]:
    words = {w.strip("-") for w in WORD.findall(text.lower()) if w not in STOPWORDS}
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words}  # crude plural folding: "creators" matches "creator"


def score_destination(rendition: Rendition, row: dict[str, Any]) -> Relevance:
    """`row` is a catalogue row (see Catalogue.get)."""
    dest = row["destination"]
    rules = dest.get("rules") or {}
    content_words = keywords(" ".join([rendition.title, rendition.body, " ".join(rendition.tags)]))
    topic_words = keywords(" ".join([*(row.get("topics") or []), dest.get("name", ""), (dest.get("extra") or {}).get("site_title") or ""]))
    tag_words = keywords(" ".join(rendition.tags))
    overlap = sorted(content_words & topic_words)
    tag_hits = sorted(tag_words & topic_words)
    topic_points = min(3.0, 1.0 * len(tag_hits) + 0.75 * len([w for w in overlap if w not in tag_hits]))
    language_ok = (not row.get("language")) or row["language"] == (rendition.language or "en").split("-")[0]
    language_points = 1.0 if language_ok else 0.0
    vetoes = []
    if rendition.promotional and rules.get("self_promo_allowed") is False:
        vetoes.append("promotion is not allowed there")
    if rendition.link_url and rules.get("link_policy") == "none":
        vetoes.append("links are not allowed there")
    fit_points = 0.0 if vetoes else 1.0
    health_points = 0.5 if row.get("status") == "certified" else 0.0

    total = topic_points + language_points + fit_points + health_points  # 0 to 5.5
    score = max(1, min(5, int(total + 0.5)))  # round to the nearest whole point, 1 to 5
    if topic_points == 0:
        score = min(score, 2)  # being in the right language and healthy is not relevance: no shared topic, never above 2
    if vetoes:
        score = 1
    parts = []
    if overlap:
        parts.append("shared topics: " + ", ".join(overlap[:4]))
    else:
        parts.append("no shared topics")
    parts.append("same language" if language_ok else f"language differs ({row.get('language')} vs {rendition.language})")
    if vetoes:
        parts.append("blocked: " + "; ".join(vetoes))
    if health_points:
        parts.append("certified")
    explanation = f"{score}/5: " + "; ".join(parts) + "."
    return Relevance(destination_id=row["id"], score=score, explanation=explanation,
                     signals={"topic_points": round(topic_points, 2), "language_points": language_points, "fit_points": fit_points,
                              "health_points": health_points, "shared_words": overlap[:10], "vetoes": vetoes})


def rank(rendition: Rendition, rows: list[dict[str, Any]], top: int | None = None) -> list[Relevance]:
    scored = [score_destination(rendition, r) for r in rows]
    scored.sort(key=lambda s: (-s.score, -s.signals["topic_points"], s.destination_id))
    return scored[:top] if top else scored
