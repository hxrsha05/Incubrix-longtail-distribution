"""Measure the recommender:   python -m scripts.eval_recommender [labelled_set.json]

For each labelled post, every method ranks the 300 destinations and we count how many of its top 10 are TRULY relevant.
precision@10 = hits in the top 10 / 10 (every post has at least 10 relevant destinations, so 100% is reachable).
Methods compared, simplest first:
  random        destinations in random order (averaged over 200 shuffles): the floor
  relevance     the plain 1-5 relevance score (app/relevance.py), ties broken by name
  recommender   the idf-weighted, synonym-aware, veto-filtering recommender (app/recommender.py)
  no synonyms   the same recommender with the synonym table switched off (an ablation: how much of the result is the table?)
  content only  the recommender without the audience-fit and track-record bonuses (an ablation: do those bonuses cost or add precision on this set?)
Writes test-pack/recommender/eval.md and eval.json. See scripts/make_labelled_set.py for what the labelled set is, and is not.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

from app.models import Rendition
from app.recommender import recommend
from app.relevance import rank as relevance_rank

ROOT = Path(__file__).resolve().parent.parent
K = 10


def rows_of(destinations: list[dict]) -> list[dict]:
    return [{"id": d["id"], "name": d["name"], "topics": d["topics"], "language": d["language"], "status": d["status"],
             "destination": {"name": d["name"], "rules": d["rules"], "extra": {"site_title": d["site_title"]}}} for d in destinations]


def rendition_of(q: dict) -> Rendition:
    return Rendition(title=q["title"], body=q["body"], tags=q["tags"], language=q["language"], promotional=q["promotional"], link_url=q["link_url"])


def precision(ranked_ids: list[str], relevant: set[str]) -> float:
    return sum(1 for i in ranked_ids[:K] if i in relevant) / K


def evaluate(data: dict) -> dict:
    rows = rows_of(data["destinations"])
    ids = [r["id"] for r in rows]
    rnd = random.Random(1)
    scores: dict[str, list[float]] = {"random": [], "relevance": [], "recommender": [], "no_synonyms": [], "content_only": []}
    per_query = []
    for q in data["queries"]:
        relevant, rendition = set(q["relevant"]), rendition_of(q)
        shuffles = []
        for _ in range(200):
            order = ids[:]
            rnd.shuffle(order)
            shuffles.append(precision(order, relevant))
        p_random = statistics.mean(shuffles)
        p_relevance = precision([r.destination_id for r in relevance_rank(rendition, rows)], relevant)
        p_reco = precision([r.destination_id for r in recommend(rendition, rows, top=K)], relevant)
        p_plain = precision([r.destination_id for r in recommend(rendition, rows, top=K, synonyms=False)], relevant)
        p_content = precision([r.destination_id for r in recommend(rendition, rows, top=K, audience_weight=0.0, history_weight=0.0)], relevant)
        scores["content_only"].append(p_content)
        scores["no_synonyms"].append(p_plain)
        scores["random"].append(p_random)
        scores["relevance"].append(p_relevance)
        scores["recommender"].append(p_reco)
        per_query.append({"query": q["id"], "relevant": len(relevant), "random": round(p_random, 2), "relevance": p_relevance, "recommender": p_reco, "no_synonyms": p_plain, "content_only": p_content})
    summary = {m: round(statistics.mean(v), 3) for m, v in scores.items()}
    return {"k": K, "queries": len(data["queries"]), "destinations": len(data["destinations"]), "precision_at_10": summary, "per_query": per_query}


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "recommender" / "labelled_set.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    result = evaluate(data)
    out = ROOT / "test-pack" / "recommender"
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    p = result["precision_at_10"]
    lines = [
        "# Recommender evaluation", "",
        f"Labelled set: {result['queries']} posts, {result['destinations']} destinations (synthetic, see `scripts/make_labelled_set.py`).",
        f"Measure: precision@{K} = how many of the top {K} suggestions are truly relevant, averaged over the posts.", "",
        "| Method | precision@10 |", "|---|---|",
        f"| random order | {p['random']:.1%} |", f"| plain relevance score | {p['relevance']:.1%} |", f"| recommender | {p['recommender']:.1%} |", f"| recommender without the synonym table | {p['no_synonyms']:.1%} |", f"| recommender, content words only (no audience or history bonus) | {p['content_only']:.1%} |", "",
        "## Per post", "", "| post | truly relevant | random | relevance | recommender | no synonyms | content only |", "|---|---|---|---|---|---|---|",
        *[f"| {r['query']} | {r['relevant']} | {r['random']:.0%} | {r['relevance']:.0%} | {r['recommender']:.0%} | {r['no_synonyms']:.0%} | {r['content_only']:.0%} |" for r in result["per_query"]], "",
        "## What this does and does not show", "",
        "- The destinations and posts are generated, with hidden true topics and deliberately messy surface text (synonyms, missing topics, generic names, false friends).",
        "- The label rule is simple (topic overlap, same language, not refused by the destination's rules). It is not the judgement of many people.",
        "- CAUTION: the recommender's synonym table and this labelled set were both written by the AI that built the project, so the vocabularies overlap and the full figure is optimistic. The row without the synonym table shows how much of the result does not depend on it.",
        "- So the number shows how well the recommender copes with messy descriptions. It is not a promise about real communities.",
    ]
    (out / "eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:10]))


if __name__ == "__main__":
    main()
