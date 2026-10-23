"""Recommender (Outstanding level): which destinations should this content go to? Best first, never a blocked one.

It improves on the plain relevance score in three ways:
  * words are WEIGHTED by how rare they are in the catalogue (idf): "kubernetes" says more about a destination than "community";
  * word endings are folded together (creator / creators / creating) and a small synonym table links obvious pairs;
  * destinations that would refuse the content (promotion to a no-promo place, links to a no-link place, wrong language
    when the destination has one) are removed, not just ranked low.
The result carries the 1-5 relevance score and its explanation for each item, so a person can see why it was suggested.
Quality is MEASURED, not claimed: scripts/eval_recommender.py computes precision at 10 on a labelled set.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from pydantic import BaseModel

from app.models import Rendition
from app.relevance import STOPWORDS, WORD, score_destination

SYNONYMS = [  # groups of words that mean (nearly) the same thing in this domain
    {"video", "film", "movie", "clip", "footage", "youtube"},
    {"edit", "editing", "editor", "post-production"},
    {"ai", "artificial", "machine-learning", "ml", "llm", "gpt"},
    {"dev", "developer", "programming", "coding", "software", "engineering"},
    {"startup", "founder", "entrepreneur", "saas", "bootstrapped"},
    {"design", "ui", "ux", "designer", "figma"},
    {"podcast", "audio", "radio", "episode"},
    {"opensource", "open-source", "oss", "foss"},
    {"marketing", "growth", "seo", "newsletter", "audience"},
    {"photo", "photography", "camera", "lens"},
    {"game", "gaming", "gamedev", "indie-game"},
    {"music", "song", "producer", "beat"},
    {"write", "writing", "author", "blogging", "blog", "writer"},
]
_CANON = {w: min(group) for group in SYNONYMS for w in group}


def stem(word: str, synonyms: bool = True) -> str:
    word = word.lower().strip("-")
    for suffix in ("ing", "ers", "er", "es", "s", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)]
            break
    return _CANON.get(word, word) if synonyms else word


def terms(text: str, synonyms: bool = True) -> list[str]:
    return [stem(w, synonyms) for w in WORD.findall(text.lower()) if w not in STOPWORDS]


class Recommendation(BaseModel):
    destination_id: str
    score: int  # 1 to 5, the relevance score
    similarity: float  # 0 to 1, the weighted overlap that decided the order
    explanation: str
    matched_terms: list[str]


def _descriptor(row: dict[str, Any]) -> str:
    dest = row["destination"]
    return " ".join([row.get("name", ""), dest.get("name", ""), " ".join(row.get("topics") or []), (dest.get("extra") or {}).get("site_title") or ""])


def recommend(rendition: Rendition, rows: list[dict[str, Any]], top: int = 10, synonyms: bool = True) -> list[Recommendation]:
    if not rows:
        return []
    docs = {r["id"]: Counter(terms(_descriptor(r), synonyms)) for r in rows}
    n = len(rows)
    df: Counter[str] = Counter(t for d in docs.values() for t in d)
    idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}  # a term in few destinations weighs more
    # content terms: tags count three times, the title twice, the body once
    content: Counter[str] = Counter()
    shown: dict[str, str] = {}  # internal term -> the word the content actually used, for the explanation
    for w in WORD.findall(" ".join([*rendition.tags, rendition.title, rendition.body]).lower()):
        if w not in STOPWORDS:
            shown.setdefault(stem(w, synonyms), w)
    for t in terms(" ".join(rendition.tags), synonyms):
        content[t] += 3
    for t in terms(rendition.title, synonyms):
        content[t] += 2
    for t in terms(rendition.body, synonyms):
        content[t] += 1
    norm_c = math.sqrt(sum((c * idf.get(t, 0.0)) ** 2 for t, c in content.items())) or 1.0
    out: list[Recommendation] = []
    for row in rows:
        relevance = score_destination(rendition, row)
        if row.get("status") == "demoted":
            continue  # failed its last certification: not a suggestion
        if relevance.signals["vetoes"]:
            continue  # it would refuse this content: do not suggest it at all
        if row.get("language") and row["language"] != (rendition.language or "en").split("-")[0]:
            continue  # a destination in another language is not a suggestion (the tailoring step does not translate)
        d = docs[row["id"]]
        shared = [t for t in d if t in content]
        dot = sum(content[t] * idf[t] * d[t] * idf[t] for t in shared)
        norm_d = math.sqrt(sum((c * idf[t]) ** 2 for t, c in d.items())) or 1.0
        similarity = dot / (norm_c * norm_d)
        bonus = 0.02 if row.get("status") == "certified" else 0.0  # a proven destination wins a tie
        out.append(Recommendation(destination_id=row["id"], score=relevance.score, similarity=round(similarity + bonus, 4), matched_terms=sorted({shown.get(t, t) for t in shared})[:8],
                                  explanation=relevance.explanation if shared else relevance.explanation + " (no weighted overlap)"))
    out.sort(key=lambda r: (-r.similarity, -r.score, r.destination_id))
    return out[:top]
