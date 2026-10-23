"""Build the labelled set used to MEASURE the recommender:   python -m scripts.make_labelled_set

HONEST DESCRIPTION OF THE SET (also written into the file):
  * It is SYNTHETIC. 300 imaginary destinations and 30 imaginary posts, generated with a fixed seed.
  * Every destination and post has HIDDEN true topics. The recommender never sees them. It only sees surface text: names,
    listed topics, site descriptions, and the post's title, body and tags.
  * The label "relevant" = the destination's true topics overlap the post's true topics, the destination is in the post's
    language, and the destination would not refuse the post (no-promo / no-link rules).
  * The surface text is deliberately messy: synonyms instead of the plain word, topics left out of the listed topics,
    generic names, and "false friends" (a gaming forum whose name contains "footage").
So the measured precision tells how well the recommender copes with messy descriptions. It does NOT prove performance on
real communities, and the labels follow a simple rule, not the judgement of many people. A person should review the file.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "samples" / "recommender" / "labelled_set.json"
TOPICS = {
    "video": ["video", "film", "footage", "clips", "movie", "youtube"],
    "ai": ["ai", "machine-learning", "llm", "neural", "gpt", "models"],
    "devtools": ["developer", "programming", "coding", "software", "engineering", "devops"],
    "startups": ["startup", "founder", "saas", "entrepreneur", "bootstrapped", "launch"],
    "design": ["design", "ui", "ux", "figma", "typography", "designer"],
    "podcasting": ["podcast", "audio", "episode", "radio", "interview", "microphone"],
    "opensource": ["open-source", "oss", "foss", "github", "maintainer", "contributors"],
    "marketing": ["marketing", "seo", "growth", "newsletter", "audience", "campaign"],
    "photography": ["photo", "photography", "camera", "lens", "portrait", "lightroom"],
    "gaming": ["game", "gaming", "gamedev", "esports", "indie-game", "speedrun"],
    "music": ["music", "song", "producer", "beat", "guitar", "synth"],
    "writing": ["writing", "author", "blog", "novel", "writer", "storytelling"],
    "cooking": ["recipe", "cooking", "kitchen", "baking", "chef", "meals"],
    "fitness": ["fitness", "workout", "running", "gym", "marathon", "training"],
}
FILLER = ["Hub", "Corner", "Lounge", "Collective", "Exchange", "Club", "Network", "Place", "Circle", "Community"]
GENERIC = ["The Daily Hangout", "Friendly Folks", "Open Mic", "Coffee Table Talks", "The Big Room", "Weekend Chat"]
BODY_FILLER = ["We have been working on this for a while.", "It is free to try.", "Feedback is very welcome.", "Here is what we learned.", "More details are below."]
LANGS = ["en"] * 7 + ["de", "de", "fr"]


def main() -> None:
    rnd = random.Random(7)
    names = list(TOPICS)
    destinations = []
    for i in range(300):
        true = rnd.sample(names, rnd.choice([1, 1, 2, 2, 3]))
        words = [rnd.choice(TOPICS[t]) for t in true]
        listed = [t for t in true if rnd.random() > 0.2] or true[:1]  # sometimes a true topic is not listed
        generic = rnd.random() < 0.15
        name = rnd.choice(GENERIC) + f" {i}" if generic else " ".join(w.replace("-", " ").title() for w in words[:2]) + " " + rnd.choice(FILLER) + f" {i}"
        if rnd.random() < 0.08:  # a false friend: a word that belongs to another topic
            other = rnd.choice([t for t in names if t not in true])
            name = name.replace(f" {i}", f" {rnd.choice(TOPICS[other][:3]).title()} {i}")
        site_title = ("A place for " + " and ".join(words)) if rnd.random() < 0.5 else None
        language = rnd.choice(LANGS)
        rules = {"frequency_cap": None, "self_promo_allowed": rnd.random() > 0.35, "link_policy": rnd.choice(["allowed", "allowed", "max:1", "none"]),
                 "disclosure_required": False, "automation_allowed": True}
        destinations.append({"id": f"d{i:03d}", "name": name, "topics": [rnd.choice(TOPICS[t]) if rnd.random() < 0.4 else t for t in listed], "language": language,
                             "status": "uncertified", "site_title": site_title, "rules": rules, "true_topics": true})
    queries = []
    for q in range(30):
        language = "en" if q < 27 else "de"
        for _attempt in range(200):  # draw posts until at least 10 destinations are truly relevant, so precision at 10 can reach 100%
            true = rnd.sample(names, rnd.choice([1, 1, 2]))
            promotional, link = rnd.random() < 0.3, rnd.random() < 0.5
            relevant = [d["id"] for d in destinations
                        if set(d["true_topics"]) & set(true) and d["language"] == language
                        and not (promotional and not d["rules"]["self_promo_allowed"]) and not (link and d["rules"]["link_policy"] == "none")]
            if len(relevant) >= 10:
                break
        pool = [w for t in true for w in TOPICS[t]]
        title_words = rnd.sample(pool, 2)
        body_words = rnd.sample(pool, 3)
        tags = [t for t in true if rnd.random() < 0.5]
        queries.append({"id": f"q{q:02d}", "title": f"New {title_words[0].replace('-', ' ')} and {title_words[1].replace('-', ' ')} update",
                        "body": f"We are sharing something about {body_words[0]}, {body_words[1]} and {body_words[2]}. " + " ".join(rnd.sample(BODY_FILLER, 2)),
                        "tags": tags, "language": language, "promotional": promotional, "link_url": "https://example.com/post" if link else None,
                        "true_topics": true, "relevant": relevant})
    thin = [q["id"] for q in queries if len(q["relevant"]) < 10]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"description": __doc__.strip(), "seed": 7, "destinations": destinations, "queries": queries}, indent=1), encoding="utf-8")
    print(f"wrote {len(destinations)} destinations and {len(queries)} queries to {OUT}; queries with fewer than 10 relevant destinations: {thin or 'none'}")


if __name__ == "__main__":
    main()
