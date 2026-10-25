# REPORT: IncuBrix Long-Tail Distribution (PS4)

## 1. What this is

A service that posts content to many small destinations (forums, blogs, newsletters, partner sites, feeds) safely.
It has nine connectors, a destination catalogue where a new forum is one row, rules that are checked before every post,
retries that never post twice, and a certification run that proves a destination works and saves the evidence.
Everything runs with `docker compose up`, with no real accounts needed. The code, tests and evidence are in this repository;
`docs/EVIDENCE_LOG.md` links every claim to its proof.

## 2. Approach and the decisions that mattered

1. **Connector versus destination.** A connector knows one platform type (API calls, limits, error mapping, in a YAML manifest).
   A destination is one forum: a catalogue row with its address, community, limits and rules. Adding a forum is data, not code.
   A destination's own limits override the connector's defaults.
2. **A post is safe to retry.** The pipeline is idempotency first, rules second, connector last. After a timeout the service does
   not guess: it asks the platform whether the post exists (`reconcile`) and only re-posts when it can show that it does not.
   Ghost and the feed use a deterministic identifier derived from the idempotency key, so the check is exact.
3. **Rules fail closed.** Frequency cap, self-promotion, links, AI disclosure and automation are checked before anything leaves the
   service. A destination with missing rules is refused. A demoted destination is refused too.
4. **Certification must never leave a mess.** Each run publishes a labelled test post, confirms it is live, checks its status,
   deletes it and confirms it is gone. The post is always cleaned up, also when a step fails, and within 10 minutes. If a publish
   times out, the harness looks for the post and deletes it. A 429 is waited out (with a cap) and never hammered. A batch stops
   calling a connector after three failures in a row.
5. **Two kinds of failure.** A platform that changes its answer (a renamed field) or rejects our valid post **fails** certification and
   the destination is **demoted**. A rate limit, an expired token or an outage is **inconclusive** and only marks the destination
   "needs attention", because those do not prove the connector is broken.
6. **AI proposes, a person decides.** The rules-capture feature proposes rules from a community's rules page with a verbatim quote for
   each. Quotes are checked against the page (an invented rule is dropped). Nothing applies until a named person confirms, and
   making a destination looser needs an explicit acknowledgement.
7. **Email never sends by itself.** The Kit connector creates drafts only; a scheduled send must be asked for explicitly.
   Certification therefore can never email a subscriber.

## 3. What works (with numbers)

| Area | Result | Proof |
|---|---|---|
| Tests | 453 pass, 18 live tests skipped unless the real Lemmy and Ghost are running; also run on a fresh clone with everything removed first (444 passed then); `tests/test_hard_cases.py` runs every hard case from the brief end to end through the API | `python -m pytest`; screenshot in `evidence/manual-demos/tests/` |
| Certification | 9 destinations, 9 connectors, **9 passed, 0 failed** in one batch on a fresh clone; mean time Lemmy 0.26 s, Ghost 0.26 s, the rest 0.01 to 0.03 s | `evidence/certification/` |
| Reliability | 200 of 200 simulated publishes; 20 of 20 full certification cycles on each of the real local Lemmy and Ghost; **0 duplicates in 120 retry cases** | `test-pack/reliability/report.md` |
| Rule safety | 0 missed of 12,317 rule violations, 0 false blocks of 3,240 legal posts (my own 15,557-case set) | `test-pack/rule-safety/report.md` |
| Public demo | live on a free Render instance with a token and a rate limit; 10 of 10 checks pass on the final code (health, 401 without a token, publish, replay, timeout then retry with 0 duplicates, export); cold start 41.5 s | `scripts/check_live_demo.py`, `evidence/manual-demos/live-demo/` |
| Real platforms | Lemmy 0.19 and Ghost 6 run in containers and are posted to, read back and deleted for real | `tests/live/`, screenshots in `evidence/manual-demos/` |
| Safe retry | Timeout after the platform created the post, same-key retry: 1 post, **0 duplicates** | `test-pack/failure-demos/transcript.md` |
| Hard cases | 429 with reset time, renamed field then demotion, expired token, content rejected, too long, promotion to a no-promo place, partner revokes permission, disclosure required | `docs/EVIDENCE_LOG.md` (hard-case table) |
| CSV import | 1,000 rows in **19 s** (limit 300 s): 762 accepted, 108 duplicates, 60 invalid, 70 unreachable, 46 rows with language warnings | `test-pack/csv-import/sample-1000/` |
| Recommender | precision@10 **96.7%** versus 82.0% for the plain relevance score and 9.0% for random; 94.3% without the synonym table | `test-pack/recommender/eval.md` |
| Recertification | a failed re-check demotes; a demoted destination cannot be posted to and is not recommended; a later pass promotes it back | `tests/test_recertify.py`, `test-pack/recertification/` |
| Contract | all seven contract methods on all nine connectors through one HTTP door; `openapi.yaml` is generated and a test fails if it is stale | `tests/test_contract.py`, `tests/test_openapi.py` |
| Integration Standard | Idempotency-Key honoured on every POST under /api/v1 (a test walks the OpenAPI document), slow work is a job, errors are JSON with listed codes, the batch runner calls the API, every result has a manifest | `tests/test_http_guard.py`, `tests/test_cli.py` |
| Public demo safety | an access token and a per-client rate limit can be switched on | `app/http_guard.py`, `tests/test_http_guard.py` |

## 4. What does not work, or is not proven

- **Discourse, Dev.to, Hashnode, Kit and the signed webhook are verified against a mock, not the real platforms.** The mock is modelled on their documented
  behaviour and can fail on purpose, but a real platform may differ. Lemmy, Ghost and the feed are verified end to end. For Kit I read the official
  API reference and fixed my connector to send every required field. For Hashnode, its own announcement confirms the `publishPost` mutation but also says
  writes need the publication to be on Hashnode Pro, and I could not confirm the `removePost` name from official documentation (`SOURCES.md`).
- **Dev.to has no article delete.** "Delete" there means unpublish, and certification records that honestly.
- **The recommender's number is optimistic.** The labelled set is synthetic (300 generated destinations, 30 generated posts, hidden true
  topics, messy descriptions) and the synonym table and the set were written by the same author. It shows the method copes with messy
  descriptions; it does not predict performance on real communities.
- **The AI rules step was run live on 15 communities only, with Google Gemini (free tier).** 5 of the 15 pages state at least one of the five rules (6 rules verified, quote checked on the page, 0 dropped); the others state none. The proposals are unconfirmed and a few are judgement calls (for example "avoid overly promotional posts" was read as self-promotion allowed). The other 137 catalogue entries have not been read by the AI step. The Claude adapter has not been run against the live API; it is tested against a fake API only. The default is an
  offline keyword reader.
- **The real catalogue (`catalogue/real/`) is listing only, and its rules are mostly unconfirmed.** 152 real communities, platforms and publications were
  researched by five AI research agents and checked by `scripts/build_real_catalogue.py` against the real internet: 137 answered the importer,
  10 blocked it (HTTP 403) and 5 forbid automated visits in robots.txt (kept with a warning, found live by the research step). Rules were proposed
  for only 13 destinations (5 by the Gemini AI reader from the live page, 8 by the keyword reader from stored verbatim quotes); the other 139 keep the conservative
  default, and none is confirmed by a person. `geo` was not researched. All 152 are flagged catalogue only: nothing is ever certified or posted
  to them. Several entries are company blogs rather than independent publications, one (Search Engine Journal) is invite-only, and
  member counts exist only where a site states one.
- **Import speed is measured on test sites that share one host,** where politeness (one request per host at a time) serialises the checks.
  A real list over many hosts would be faster, but that is not measured.
- **Untested at scale:** one instance, SQLite in tests and Postgres in compose, no load test beyond the 1,000-row import.

## 5. Cost

No paid service is called by the default path, so the measured cost of certification, import, tailoring, relevance and the
recommender is **0**. The only possible cost is the optional AI reading of a rules page: the live run on 15 pages used 20,108 input tokens
and 388 output tokens on Google's free tier, so it cost $0; on a paid plan it would be a few cents. The manifest records tokens and, once the
provider prices are set in the settings, the cost. The public demo runs on a free Render instance ($0).

## 6. How AI was used

AI (Claude Code) wrote most of the code, tests and documentation; five Claude research agents gathered the real catalogue; Google Gemini ran the AI rules step on 15 pages. `AI_USE.md` lists every component, the decisions I made, and the corrections
along the way (including faults that the tests or Docker caught in the AI's own work: a race in import uploads, a startup crash on a
newer FastAPI, a truncation rule that cut words, an over-generous relevance score).

## 7. Next steps

1. Run Discourse, Dev.to, Hashnode and Kit against real test accounts and replace the mocks' modelled behaviour with observed behaviour.
2. Run the AI rules step on the remaining 137 catalogue pages (the free quota allowed only 15 in the time available), and have a person confirm each captured rule set.
3. Replace the synthetic labelled set with labels from several people on real communities, and re-measure.
4. Add media upload for destinations that accept images and video, with a per-destination media check.
5. Load-test imports and batch certification with many hosts, and add per-host rate limits to the importer.
