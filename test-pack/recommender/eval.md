# Recommender evaluation

Labelled set: 30 posts, 300 destinations (synthetic, see `scripts/make_labelled_set.py`).
Measure: precision@10 = how many of the top 10 suggestions are truly relevant, averaged over the posts.

| Method | precision@10 |
|---|---|
| random order | 9.0% |
| plain relevance score | 82.0% |
| recommender | 96.7% |
| recommender without the synonym table | 95.3% |
| recommender, content words only (no audience or history bonus) | 96.7% |

## Per post

| post | truly relevant | random | relevance | recommender | no synonyms | content only |
|---|---|---|---|---|---|---|
| q00 | 27 | 10% | 90% | 100% | 100% | 100% |
| q01 | 18 | 5% | 70% | 100% | 90% | 90% |
| q02 | 30 | 10% | 100% | 100% | 100% | 100% |
| q03 | 18 | 6% | 70% | 100% | 100% | 100% |
| q04 | 30 | 10% | 100% | 100% | 100% | 100% |
| q05 | 24 | 8% | 90% | 100% | 100% | 100% |
| q06 | 17 | 6% | 100% | 90% | 90% | 90% |
| q07 | 22 | 8% | 100% | 100% | 100% | 100% |
| q08 | 22 | 8% | 90% | 100% | 100% | 100% |
| q09 | 19 | 7% | 40% | 100% | 100% | 100% |
| q10 | 25 | 8% | 90% | 90% | 90% | 90% |
| q11 | 31 | 11% | 90% | 100% | 80% | 100% |
| q12 | 17 | 6% | 90% | 80% | 90% | 80% |
| q13 | 40 | 14% | 80% | 100% | 90% | 100% |
| q14 | 24 | 9% | 100% | 90% | 100% | 90% |
| q15 | 33 | 11% | 80% | 100% | 100% | 100% |
| q16 | 17 | 5% | 70% | 100% | 100% | 100% |
| q17 | 26 | 10% | 100% | 90% | 100% | 100% |
| q18 | 31 | 10% | 70% | 100% | 90% | 100% |
| q19 | 61 | 21% | 70% | 100% | 100% | 100% |
| q20 | 26 | 8% | 100% | 100% | 100% | 100% |
| q21 | 47 | 17% | 100% | 100% | 100% | 100% |
| q22 | 42 | 15% | 80% | 100% | 100% | 100% |
| q23 | 11 | 4% | 80% | 90% | 80% | 90% |
| q24 | 21 | 6% | 100% | 100% | 100% | 100% |
| q25 | 10 | 4% | 60% | 80% | 80% | 80% |
| q26 | 42 | 15% | 90% | 100% | 100% | 100% |
| q27 | 18 | 7% | 80% | 100% | 100% | 100% |
| q28 | 10 | 4% | 30% | 90% | 90% | 90% |
| q29 | 19 | 7% | 50% | 100% | 90% | 100% |

## What this does and does not show

- The destinations and posts are generated, with hidden true topics and deliberately messy surface text (synonyms, missing topics, generic names, false friends).
- The label rule is simple (topic overlap, same language, not refused by the destination's rules). It is not the judgement of many people.
- CAUTION: the recommender's synonym table and this labelled set were both written by the AI that built the project, so the vocabularies overlap and the full figure is optimistic. The row without the synonym table shows how much of the result does not depend on it.
- So the number shows how well the recommender copes with messy descriptions. It is not a promise about real communities.
