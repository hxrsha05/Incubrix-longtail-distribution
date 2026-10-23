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
| Tests | 406 pass, 18 live tests skipped unless the real Lemmy and Ghost are running; `tests/test_hard_cases.py` runs every hard case from the brief end to end through the API | `python -m pytest`; screenshot in `evidence/manual-demos/tests/` |
| Certification | 9 destinations, 9 connectors, **9 passed, 0 failed** in one batch; Lemmy 0.5 s, Ghost 0.3 s, the rest under 0.03 s | `evidence/certification/` |
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

- **Discourse, Dev.to, Hashnode and Kit are verified against a mock, not the real platforms.** The mock is modelled on their documented
  behaviour and can fail on purpose, but a real platform may differ. Lemmy, Ghost and the feed are verified end to end. For Kit I read the official
  API reference and fixed my connector to send every required field. For Hashnode, its own announcement confirms the `publishPost` mutation but also says
  writes need the publication to be on Hashnode Pro, and I could not confirm the `removePost` name from official documentation (`SOURCES.md`).
- **Dev.to has no article delete.** "Delete" there means unpublish, and certification records that honestly.
- **The recommender's number is optimistic.** The labelled set is synthetic (300 generated destinations, 30 generated posts, hidden true
  topics, messy descriptions) and the synonym table and the set were written by the same author. It shows the method copes with messy
  descriptions; it does not predict performance on real communities.
- **The Claude adapter for rules capture has not been run against the live API.** It is tested against a fake API only. The default is an
  offline keyword reader.
- **"150 real destinations researched" was not done.** It needs real research that cannot be checked from here; inventing entries would
  be worse than leaving it out. The importer is ready to take such a list.
- **Import speed is measured on test sites that share one host,** where politeness (one request per host at a time) serialises the checks.
  A real list over many hosts would be faster, but that is not measured.
- **Untested at scale:** one instance, SQLite in tests and Postgres in compose, no load test beyond the 1,000-row import.

## 5. Cost

No paid service is called by the default path, so the measured cost of certification, import, tailoring, relevance and the
recommender is **0**. The only possible cost is the optional AI reading of a rules page: one call per page, a few thousand input
tokens and a few hundred output tokens; the manifest records tokens and, once the provider prices are set in the settings, the cost.
That number was not measured because the feature was not run against the live API. Infrastructure for the demo is a small container
host (see the hosting note in the deliverables).

## 6. How AI was used

AI wrote most of the code, tests and documentation. `AI_USE.md` lists every component, the decisions I made, and the corrections
along the way (including faults that the tests or Docker caught in the AI's own work: a race in import uploads, a startup crash on a
newer FastAPI, a truncation rule that cut words, an over-generous relevance score).

## 7. Next steps

1. Run Discourse, Dev.to, Hashnode and Kit against real test accounts and replace the mocks' modelled behaviour with observed behaviour.
2. Research and import a real list of destinations with their rules, and have a person confirm each captured rule set.
3. Replace the synthetic labelled set with labels from several people on real communities, and re-measure.
4. Add media upload for destinations that accept images and video, with a per-destination media check.
5. Load-test imports and batch certification with many hosts, and add per-host rate limits to the importer.
