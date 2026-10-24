# EVIDENCE_LOG.md — what is done, where the proof is, what is still missing

> The Excel form says: *"A claim without evidence scores 0. A claim that turns out to be false disqualifies the whole
> submission."* So: only tick **Yes** when the proof exists and I have seen it work.
> **Status key:** Done = done and proven; Partial = partly done; Not started; Unverified = written but not yet proven

Last updated: 2026-10-22. Unit and integration tests: **406 passing** (`python -m pytest`, run from this folder, no keys needed). Live tests against the real local Lemmy and Ghost: **18 passing** (opt-in, see `tests/live/`).

## Must-haves (all 9 must pass)

| # | Must-have | Status | Evidence now | Still missing |
|---|---|---|---|---|
| 1 | Exact connector contract | Done | `app/connectors/base.py`, `app/api/connector_routes.py`, `tests/test_connector_framework.py`, `tests/test_contract.py` (all 7 methods on all 6 connectors through the HTTP door), `openapi.yaml` + `tests/test_openapi.py` | — |
| 2 | Six Core connectors certified | Done (nine connectors) | Real certification runs of Lemmy and Ghost (real local instances) and Discourse, Dev.to, signed webhook, assisted (mock platforms): `evidence/certification/*/run-*.txt|json`, produced by `certify_batch` through the running service (6 of 6 passed). Unit tests per connector in `tests/`, live tests in `tests/live/` | Screenshots of a live Lemmy post and a Ghost post (manual, see below); Discourse and Dev.to are verified against a mock, not the real platforms (stated honestly in the form) |
| 3 | Configuration, not code | Done | Catalogue API (`app/api/catalogue_routes.py`, `tests/test_certification.py::test_catalogue_api_...`): a new destination is one POST; per-destination spec override test | Live "add an instance" demo in the video |
| 4 | Rules checked before every post | Done | `tests/test_rules.py`, `tests/test_idempotency.py`; batch runner shows a blocked promo post: `test-pack/batch-run/04-blocked-no-promo.result.json` | Rule test set for the "caught vs missed" metric |
| 5 | Never spam, never cheat | Done | 429 + reset time (`test-pack/failure-demos/2-rate-limit.json`), certification waits out a 429 with a cap, stops after 3 consecutive failures on a connector, test posts deleted (also on failure, within 10 minutes: `tests/test_certification.py`), own local instances only | Written proof from logs for the form |
| 6 | Safe retries | Done | `tests/test_idempotency.py::test_timeout_after_create_then_retry_does_not_duplicate`; **live-service run**: `test-pack/failure-demos/transcript.md` case 1 (timeout after the platform created the post, retry with the same key, 0 duplicates); batch re-run replays instead of re-posting (`test-pack/batch-run-rerun/`) | Screenshot: `evidence/manual-demos/failure-demos/01-demo-failures-output.png` |
| 7 | Secrets protected + simulation mode | Done | Redaction in `app/logging_setup.py` + tests; secret never in errors (webhook, Discourse, Dev.to tests); `evidence/secret-scan.txt` (repo files and service logs: CLEAN); simulation mode (`README.md`, `app/credentials.py`) | Re-run `python -m scripts.secret_scan --docker-logs` just before submitting |
| 8 | Local test instances (compose) | Partial | `docker compose up` starts Lemmy, Ghost, Postgres, S3, API, mock platforms and the seed job; seed is safe to re-run | Run on a clean machine (fresh clone) |
| 9 | Integration Standard + live demo | Partial | see below; the demo can be protected with `API_TOKEN` and limited with `RATE_LIMIT_PER_MINUTE` | Deploy live demo; keep it up until results |

## Integration Standard (2 marks each)

| # | Point | Status | Evidence now | Still missing |
|---|---|---|---|---|
| 1 | One-command start and test | Done | `docker compose up -d --build`; `python -m pytest` (no keys) | Clean-machine run |
| 2 | `openapi.yaml` matching the service | Done | `openapi.yaml`, `tests/test_openapi.py` fails when stale | — |
| 3 | Background jobs (202, status, HMAC callback) | Done | `app/jobs.py`, `tests/test_jobs.py`; real job kinds `certify`, `certify_batch`, `import`, `recertify`, `rules_capture` (everything that can take over 2 seconds is a job) | — |
| 4 | Idempotency-Key on every POST | Done | a middleware (`app/http_guard.py`) covers every POST under /api/v1/: same key and request = same answer with `Idempotent-Replayed: true`, different request = 409; `tests/test_http_guard.py` walks every POST route in the OpenAPI document | — |
| 5 | Clear JSON errors | Done | `app/errors.py`, `tests/test_contract.py::test_every_error_is_the_documented_json_shape`, error table in `README.md` | — |
| 6 | Configuration from environment | Done | `app/config.py`, `.env.example`, settings table in `README.md` | — |
| 7 | Files in/out, S3 storage | Done (inputs: upload or URL) | Certification evidence is written to RustFS and returned as presigned download links (`app/evidence.py`; real run through compose: `certify_batch` result links) | Open one link in the browser for the video |
| 8 | JSON logs + `/health` + `/ready` | Done | `app/logging_setup.py`, `tests/test_foundation.py` | — |
| 9 | Output manifest per result | Done | `app/output_manifest.py`; in every certification record, publish result, import result and rules proposal: versions, models/providers, seed, settings, timings, cost, warnings, licences, AI-generated label | — |
| 10 | Batch runner CLI | Done | `app/cli.py` runs a folder, a JSON list or a JSONL file against the API (`POST /api/v1/publish`) and writes everything to one folder; `tests/test_cli.py` (also against a real protected server); outputs in `test-pack/batch-run/` and `test-pack/batch-run-rerun/` | — |

## Levels

**Core:** each Core connector passes full certification (publish → live → status → delete) with stored evidence **Done** ·
errors mapped for expired token / rate limit / rejected content **Done** (every connector has tests for these)

**Advanced — ALL five required, or the level scores 0 (and screening cannot reach 60):**
- [x] CSV import: validate, normalise, de-duplicate (file, catalogue, and by final address plus platform after redirects), reachability, enrichment (member count for Lemmy and Discourse, rules page link, platform, category, language, audience relevance), row-by-row report; the CSV may be uploaded or given as an address. **1,000 rows in 19 s** (limit 300 s). `app/csv_import.py`, `tests/test_csv_import.py` (34 tests), outputs in `test-pack/csv-import/sample-1000/` (input, report.csv, report.json, summary.txt). Note: all 1,000 test sites sit on one host, so the one-request-per-host politeness rule serialises them; a real list spread over many hosts is faster
- [x] Rules capture: AI reads a rules page → proposes rules with quotes → **a person confirms** before use: `app/rules_capture.py`, `app/api/rules_routes.py`, `tests/test_rules_capture.py` (22 tests). Quotes are verified against the page (an invented rule is dropped), nothing applies until a named person confirms, loosening needs an extra acknowledgement. The offline mock reader is the default; the Claude adapter (`RULES_EXTRACTOR=anthropic`) is tested against a fake API only and is **not verified against the live API** (no key used)
- [x] Relevance score 1–5 against IncuBrix's audience (AI, video, marketing, SaaS, creators, founders), with a reason for each destination: `app/audience.py`, shown as `relevance_score` and `relevance_reason` on every destination record and in the import report, `tests/test_catalogue_record.py`. Rule-based, repeatable, free. (A second score, `app/relevance.py` / `POST /api/v1/relevance`, rates how well one POST suits one destination)
- [x] Tailoring: `app/tailoring.py`, `POST /api/v1/tailor`, `tests/test_tailoring.py` (26 tests): word-boundary truncation, link policy, mention style, tag style or hashtags, canonical URL, AI disclosure never cut, language warning; a test proves the tailored output passes every connector's own validate()
- [x] Three more connectors: **MRSS feed** (`app/connectors/feed.py`, `app/feeds.py`, `app/api/feed_routes.py`: a real feed host, valid Media RSS), **Hashnode** (`app/connectors/hashnode.py`, GraphQL, errors read from the body) and **Kit** (`app/connectors/kit.py`, API v4 broadcasts, drafts only so it can never email subscribers). Tests: `tests/test_new_connectors.py` (19), contract and certification tests cover them too. Hashnode and Kit are built against the documented APIs and a mock, **not verified against real accounts**; the feed is verified end to end because we host it

**Advanced demo against the running service:** `python -m scripts.demo_advanced` -> `test-pack/advanced-demos/transcript.md` (tailoring, relevance with explanations, rules capture with quotes, human confirmation, a second confirmation refused).

**Outstanding (stretch):**
- [x] Recommender (ranks by content fit, audience relevance, rules and track record, with a reason) with measured top-10 precision: `app/recommender.py`, `POST /api/v1/recommend`, `scripts/eval_recommender.py`, labelled set `samples/recommender/labelled_set.json`, results `test-pack/recommender/eval.md`. **precision@10: recommender 96.7%, plain relevance score 82.0%, random 9.0%; without the synonym table 94.3%.** The set is SYNTHETIC (300 generated destinations, 30 generated posts, hidden true topics, messy surface text) and the synonym table and the set were written by the same author, so the figure is optimistic and says nothing certain about real communities. `tests/test_recommender.py`
- [x] `fetch_metrics` on 5 connectors: Lemmy, Discourse, Dev.to, Hashnode, Kit (tests in `tests/test_discourse_devto_connectors.py`, `tests/test_new_connectors.py`, `tests/test_lemmy_connector.py`)
- [x] Recertification scheduler with auto-demotion and an alert (ticket, `GET /api/v1/alerts`, optional signed webhook); destinations are always retested before 30 days (a larger setting is lowered): `app/recertify.py`, `app/api/recert_routes.py`, `tests/test_recertify.py` (13 tests, fake clock). Certified destinations are rechecked after 7 days, demoted ones after a 1-day cooldown (a pass promotes them back), needs_attention after 1 hour; uncertified ones never automatically. A demotion blocks publishing (`DEST_SPEC_VIOLATION`, rule `destination_demoted`) and removes the destination from recommendations. Off by default (`RECERT_INTERVAL_S=0`); run by hand with `POST /api/v1/recertify`. Live demo (run against the pinned Docker image): `python -m scripts.demo_recertification` -> `test-pack/recertification/transcript.md`: certified -> field renamed -> demoted -> publishing refused with `destination_demoted` and not recommended -> platform fixed -> promoted back -> publishing works
- [x] 150 real destinations researched: **152 listed** in `catalogue/real/real_catalogue.csv` (the destination record), with `import_report.csv` (status, response time, final address, platform, language), `rules_evidence.json` and `SUMMARY.md`. Built on 23 Oct 2026 by `python -m scripts.build_real_catalogue` from `samples/research/*.json` (168 candidates, 16 duplicates removed). 137 answered HTTP 200, 10 blocked the importer (403), 5 forbid automated visits (robots.txt); rules proposed from quotes for 9 destinations, rules unconfirmed, geo not researched, all catalogue only (never posted to)

## Hard cases (tab `6_Hard_cases`) — each needs a test and a saved output

| Hard case | Status | Evidence / test |
|---|---|---|
| Publish times out, retried with same key | Done | `test_timeout_after_create_then_retry_does_not_duplicate`; live service: `test-pack/failure-demos/1-timeout-then-retry.json` |
| 429 with reset time | Done | `test_http_429_with_retry_after`; live service: `test-pack/failure-demos/2-rate-limit.json` |
| Expired token | Done | `test_wrong_password_is_auth_expired`; `test-pack/failure-demos/4-no-credentials.json` |
| Content rejected by platform | Done | `test_platform_refusal_is_content_rejected`, `test_discourse_content_rules_become_a_rejection` |
| Title/body too long | Done | `test_validate_reports_field_level_violations` |
| Media the destination does not accept | Done | `tests/test_media.py`, `tests/test_hard_cases.py::test_media_the_destination_does_not_accept_is_refused_and_says_which_item` (field `media[1]`, nothing sent) |
| Second post inside frequency cap | Done | `test_second_post_inside_the_cap_is_blocked_with_a_reason` |
| Promo post to a no-promo community | Done | `test_promo_to_no_promo_destination_never_reaches_the_platform`; `test-pack/batch-run/04-blocked-no-promo.result.json` |
| Platform renames a field → certification fails, destination demoted | Done | `test_a_renamed_field_fails_certification_demotes_and_leaves_no_stray_post`; live service: `test-pack/failure-demos/3-renamed-field.json` |
| CSV with duplicates, bad URLs, mixed languages | Done | `test_every_kind_of_row_gets_the_right_status_and_reason`; 1,000-row run: 108 duplicates, 60 invalid, 70 unreachable, language-mismatch warnings (`test-pack/csv-import/sample-1000/report.csv`) |
| Partner revokes permission | Done | `test_a_partner_who_revokes_permission_is_an_auth_error_not_a_retry` (HTTP 410) |
| Non-English community | Not started | add language to a catalogue row (field exists) and a non-English tailoring test |
| Destination requires AI disclosure | Done | `tests/test_rules.py::test_ai_disclosure_required` |

## Metrics to measure (tab `5_Metrics`)

- [x] Certification: 9 destinations (9 connectors) certified in one `certify_batch` through the running service, 9 passed, 0 failed (Lemmy 0.5 s, Ghost 0.3 s, others under 0.03 s); evidence links in `evidence/certification/`
- [ ] Reliability: publish success rate (simulation + local); **duplicates under retry = 0** (shown once live; measure over many runs)
- [ ] Rule safety: violations caught vs missed on a rule test set (any miss is serious)
- [x] Catalogue (Advanced): 1,000 rows -> 762 accepted, 108 duplicates, 60 invalid, 70 unreachable, in 19 s (53 rows/s, 32 workers, cost 0)
- [ ] Recommendation (Outstanding): top-10 precision on my labelled set
- [x] Cost: no paid API or model is called so far (manifest records 0); update when the AI features are added

## Deliverables checklist

- [x] Private GitHub repo, `admin@incubrix.com` invited, real commit history (commit small and often)
- [ ] Live demo URL + token + cold-start time, rate-limited, **up until results are announced**
- [x] README (architecture diagram, setup, run tests + batch runner, all settings, error-code list) — re-check the setup steps on a clean machine
- [x] `openapi.yaml`
- [x] `REPORT.md` drafted (approach, results with numbers, what does not work, cost, AI use, next steps): re-read it before submitting and update the test count if it changed
- [ ] `SOURCES.md` and `AI_USE.md` (AI_USE kept current; all licence links still to verify)
- [x] Test pack: inputs in `samples/`, outputs including failures in `test-pack/`, index linking each claim to a file and the command that made it: `test-pack/INDEX.md`
- [x] Certification pack: per destination — run record (`evidence/certification/<destination>/run-*.json|txt`); live URL and deletion confirmation are inside each record; screenshots are manual (below)
- [ ] Catalogue files: CSVs and import reports (+ labelled set if Outstanding)
- [ ] Demo video ≤ 8 min, **unedited**: one clean run · one failure and how it's handled · tests running · one design decision explained in code
- [ ] Excel form `Assessment4_YourName.xlsx`: all tabs complete, every claim linked, **Declaration signed**
- [ ] Final Submission Form submitted **and** the Excel attached to a reply to the invitation email (do both until told otherwise)

## Manual captures still wanted (screenshots the AI cannot take)

- [x] Lemmy UI showing the batch-runner post: `evidence/manual-demos/lemmy/01-batch-runner-post-live.png`
- [x] Certification summary opened from a presigned S3 link: `evidence/manual-demos/certification/01-s3-download-link-ghost-run.png`
- [x] Terminal with `python -m pytest` finishing green: `evidence/manual-demos/tests/01-pytest-208-passed.png`
- [x] Terminal with `python -m scripts.demo_failures` output: `evidence/manual-demos/failure-demos/01-demo-failures-output.png`

## Final-day safety checks

- [ ] Fresh clone → `docker compose up` works on a clean setup
- [ ] Search repo, logs, screenshots and the Excel for tokens/passwords/keys (`python -m scripts.secret_scan --docker-logs`)
- [ ] **Remove the private study files `docs/GLOSSARY.md` and `docs/STUDY_TRACKER.md` (git-ignored, never committed) and confirm no personal notes are in the repo or the Excel**
- [ ] Every link in the form opens for `admin@incubrix.com`
- [ ] Nothing in the form is ticked "Yes" without proof
- [ ] Submit **before the last hour** (deadline: Sun 25 Oct 2026, 11:59 PM IST)

## Findings from live testing (material for REPORT.md)

Checked against the real local Lemmy 0.19.20, not assumed:

| Finding | What we did about it |
|---|---|
| Lemmy 0.19.x needs `database.uri` in its config. The `database.connection` key from Lemmy's development branch is silently ignored and Lemmy then tries `localhost`. | Fixed in `docker-compose.yml`. |
| Two logins in the same second fail with HTTP 400 `duplicate key ... login_token_pkey` (the login token is built from the current second). | Connector waits 1.1 s and retries, up to 4 attempts. Unit-tested and live-tested. |
| Wrong password and missing/invalid token both return HTTP 401 `incorrect_login`. | Mapped to `CONNECTION_AUTH_EXPIRED`. |
| Title limit is 200 characters (201 is rejected as `invalid_post_title`). Body limit is **50,000**, not 10,000 as first assumed (`invalid_body_field`). | Manifest corrected to 50,000. |
| Default post rate limit is 6 requests per 10 minutes. The error is HTTP **400** `rate_limit_error` with **no reset time**; failed requests count too. | Mapped to `PLATFORM_RATE_LIMIT`. The reset time is not provided by Lemmy, so none is reported. The seed job relaxes the limit on the local test instance only. |
| Right after a rate-limit response, the next request on the same connection hung once until timeout. Not reproduced with the limits relaxed. | Timeouts are handled and treated as outcome-unknown (see idempotency). Worth watching. |
| A missing community returns HTTP 404 `couldnt_find_community`. | Reported as `PLATFORM_CONTENT_REJECTED`. |
| Ghost refuses admin passwords containing words such as "ghost" or "local" (site name). | Local default changed. |
| `minio/minio` is no longer published on Docker Hub. | Replaced with RustFS (Apache-2.0). |
| PyPI was very slow on the development network (about 56 KB/s). | Optional `PIP_INDEX_URL` build argument; the repository default stays the official PyPI. |

Ghost, checked against the real local Ghost 6.72.0:

| Finding | What we did about it |
|---|---|
| In Ghost 6 the `secret` field of an Admin API key is already the full `id:secret` string (89 characters). The seed job had joined the id on again. | Seed job fixed; it also still handles older Ghost versions that return only the secret. |
| Every call needs a freshly signed HS256 token (`kid` = key id, audience `/admin/`, 5-minute life), sent as `Authorization: Ghost <token>`. | Implemented with the standard library (no JWT dependency); the unit-test fake Ghost verifies the signature. |
| Wrong secret, expired token and wrong audience give HTTP 401; a missing header gives 403; a garbage token gives HTTP **400** with code `INVALID_JWT`. | All mapped to `CONNECTION_AUTH_EXPIRED`. |
| Title limit is 255 characters (256 gives 422). An **empty title is accepted and silently posted as "(Untitled)"**. | `validate` now rejects an empty title for every connector. |
| A duplicate slug is silently renamed to `slug-2` instead of refused. | The slug is built from the idempotency key (`my-title-3f2a1b9c`), and `reconcile` looks the post up by that exact slug after a timeout. |
| A Ghost **draft** has an unlisted preview link (`/p/<random-id>/`) that works for anyone who has the URL. | A draft is reported as `draft` with no live URL, and the live test checks it is not on the public site or feed. |
| Windows Python cannot resolve `ghost.localhost` (browsers and containers can). | Host-side tests connect to 127.0.0.1 and send the `Host` header explicitly. |

### Demo image check (23 Oct 2026)
- `docker build -f Dockerfile.demo -t incubrix-demo .` produced a 325 MB image (first build 21 minutes on a slow network, a rebuild with cached layers 3 seconds).
- Run locally with `API_TOKEN` set: `/ready` answered 1.7 to 2.2 seconds after `docker run` (cold start measured on a developer laptop, not on the public host).
- Seeded 7 simulation destinations; a call without the token got 401; a publish with the token and an Idempotency-Key went live on the fake Discourse.
- Found and fixed: the start script did not pass the token to the seed script, so seeding got 401 (the API stayed up).

### Public demo on Render (23 Oct 2026)
- Deployed from `render.yaml` (free plan, `Dockerfile.demo`, source commit 4848748), address https://incubrix-demo.onrender.com. Deploy took 56 s after the image build; Render's health checks answered 200.
- Without a token: `/health`, `/ready` and `/docs` answer 200, every other call answers 401 UNAUTHORIZED.
- With the token (typed only on the developer's own machine): `scripts/check_live_demo.py` passed all 10 checks, screenshot `evidence/manual-demos/live-demo/01-live-demo-checks-all-pass.png`.
- Still to measure: cold start after 20 minutes idle.

### Evidence refreshed on the final code (23 Oct 2026, evening)
Run against the rebuilt docker compose stack (API image rebuilt from the final code, real local Lemmy and Ghost, S3 on):
- `python -m app.cli run` (through the API, twice): 5 inputs, 3 published, 1 blocked by rules, 1 failed cleanly -> `test-pack/batch-run`, `test-pack/batch-run-rerun`.
- `python -m app.cli certify --connector ghost`: 1 passed -> `test-pack/demo-certify`.
- `scripts.demo_failures`, `scripts.demo_advanced`, `scripts.demo_recertification`: all cases behave as described (duplicates 0 after a timeout and retry; 429 with reset time; renamed field -> demoted -> publishing refused -> promoted back). Note: two demo scripts started at the same time share one fake platform and disturb each other; run them one at a time.
- `scripts.import_demo`: 1,000 rows in 18.3 s (762 accepted, 108 duplicates, 60 invalid, 70 unreachable).
- `scripts.measure_reliability --local http://localhost:8000` -> `test-pack/reliability/report.md`: 200 of 200 simulated publishes, 0 duplicates in 120 retry cases, 20 of 20 429 answers carried the reset time, and 20 of 20 full certification cycles passed on each of the real local Lemmy (mean 0.21 s) and Ghost (mean 0.08 s).
- `python -m pytest`: 442 passed, 18 skipped (live-instance tests); secret scan clean.

### Fresh-clone test (23 Oct 2026, final safety check, must-have 8)
Followed the README exactly on a clean clone of the repository, with all containers and volumes removed first: `copy .env.example .env`, `docker compose up -d --build` (15 s with cached images; the seed step created the Lemmy community and the Ghost account by itself), `python -m scripts.seed_catalogue`.
- First attempt found a real bug: the service read the credentials file once at start, before the seed step had written it, so the Lemmy and Ghost destinations answered `CONNECTION_AUTH_EXPIRED` until a restart (batch run: 2 published and 2 failed; certification: 2 inconclusive). Fixed in `app/credentials.py` (the file is re-read when it appears or changes), test `tests/test_credentials.py`.
- Second attempt, from a new clean clone: batch run 3 published, 1 blocked by rules, 1 failed cleanly; `app.cli certify`: 9 passed, 0 failed, 0 inconclusive (including the real local Lemmy and Ghost); `python -m pytest` on the clone: 444 passed, 18 skipped.
- The same session found and fixed a flaky test: two feed items added in one clock tick tied on their timestamp (`app/feeds.py` now stamps items strictly increasing).

### Cold start of the public demo (24 Oct 2026)
- The free Render service had been idle overnight (more than 12 hours). The first request, `curl -s -o NUL -w "%{time_total}" https://incubrix-demo.onrender.com/health` from a developer laptop in India, took **41.5 s** to answer 200. Later requests answer in under 1 s (0.81 s measured by the live check on the earlier version).
- Free Render instances sleep after about 15 minutes without traffic. No uptime pinger is used.
