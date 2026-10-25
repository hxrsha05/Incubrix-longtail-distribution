# EVIDENCE_LOG.md: what is done, where the proof is, what the limits are

> The submission form says: *"A claim without evidence scores 0. A claim that turns out to be false disqualifies the whole
> submission."* So a row is marked Done only when the proof exists and it was seen working.
> **Status key:** Done = done and proven; Partial = partly done, with the limit stated.

Last updated: 2026-10-25. Tests: **453 passing, 18 skipped** (`python -m pytest`, run from this folder, no keys needed; the 18 skipped are the live tests against the real local Lemmy and Ghost, which switch on with environment variables, see `tests/live/`). The dated entries after the findings tables record each run on the final code.

## Must-haves (all 9 must pass)

| # | Must-have | Status | Evidence | Limits |
|---|---|---|---|---|
| 1 | Exact connector contract | Done | `app/connectors/base.py`, `app/api/connector_routes.py`, `tests/test_connector_framework.py`, `tests/test_contract.py` (all 7 methods on all connectors through the HTTP door), `openapi.yaml` + `tests/test_openapi.py` | Checked by my own contract tests; IncuBrix's suite was not available |
| 2 | Six Core connectors certified | Done (nine connectors) | Certification runs of Lemmy and Ghost on the real local instances (20 of 20 full cycles each, `test-pack/reliability/report.md`) and of Discourse, Dev.to, signed webhook, assisted on mock platforms: `evidence/certification/*/run-*.txt|json`, made by `certify_batch` through the running service (9 of 9 passed on the fresh-clone run) | Discourse, Dev.to, Hashnode, Kit and the webhook are verified against mock platforms I modelled, not real accounts |
| 3 | Configuration, not code | Done | Catalogue API (`app/api/catalogue_routes.py`, `tests/test_certification.py::test_catalogue_api_...`): a new destination is one POST; per-destination spec override test | A real instance of a platform I have no account for is not tested |
| 4 | Rules checked before every post | Done | `tests/test_rules.py`, `tests/test_rule_safety.py`, `tests/test_idempotency.py`; `test-pack/rule-safety/report.md` (0 missed of 12,317 violations, 0 false blocks of 3,240 legal posts); batch runner shows a blocked promo post: `test-pack/batch-run/04-blocked-no-promo.result.json` | The test set is my own; a subtle advert with no advert phrases can pass where promotion is banned |
| 5 | Never spam, never cheat | Done | 429 + reset time (`test-pack/failure-demos/2-rate-limit.json`), certification waits out a 429 with a cap, stops after 3 consecutive failures on a connector, test posts deleted (also on failure, within 10 minutes: `tests/test_certification.py`), own local instances only; the 152 real communities are catalogue only and cannot be certified or posted to (`tests/test_catalogue_only.py`) | |
| 6 | Safe retries | Done | `tests/test_idempotency.py::test_timeout_after_create_then_retry_does_not_duplicate`; live-service run: `test-pack/failure-demos/transcript.md` case 1; 0 duplicates in 120 retry cases (`test-pack/reliability/report.md`); the public demo check (`scripts/check_live_demo.py`, screenshot in `evidence/manual-demos/live-demo/`) | A timeout was forced on simulated Discourse and Dev.to, not on the real Lemmy and Ghost |
| 7 | Secrets protected + simulation mode | Done | Redaction in `app/logging_setup.py` + tests; secrets never in errors; `evidence/secret-scan.txt` (repository files and service logs: CLEAN); simulation mode (`README.md`, `app/credentials.py`) | The scan only checks the secret values it knows about |
| 8 | Local test instances (compose) | Done | `docker compose up` starts Lemmy, Ghost, Postgres, S3, API, mock platforms and the seed job. Run from a fresh clone with all containers and volumes removed (entry "Fresh-clone test" below): batch run 3 published, 1 blocked, 1 failed cleanly; 9 of 9 destinations certified; 444 tests passed on the clone | |
| 9 | Integration Standard + live demo | Done | see below; demo at https://incubrix-demo.onrender.com (token and rate limit on); `scripts/check_live_demo.py` passed all 10 checks on the final code; cold start 41.5 s | Free Render instance: sleeps when idle (about 30 to 42 s to wake); must be kept running until results |

## Integration Standard (2 marks each)

| # | Point | Status | Evidence | Limits |
|---|---|---|---|---|
| 1 | One-command start and test | Done | `docker compose up -d --build`; `python -m pytest` (no keys); fresh-clone run | |
| 2 | `openapi.yaml` matching the service | Done | `openapi.yaml`, `tests/test_openapi.py` fails when stale | |
| 3 | Background jobs (202, status, HMAC callback) | Done | `app/jobs.py`, `tests/test_jobs.py`; job kinds `certify`, `certify_batch`, `import`, `recertify`, `rules_capture` (everything that can take over 2 seconds is a job) | |
| 4 | Idempotency-Key on every POST | Done | a middleware (`app/http_guard.py`) covers every POST under /api/v1/: same key and request = same answer with `Idempotent-Replayed: true`, different request = 409; `tests/test_http_guard.py` walks every POST route in the OpenAPI document | |
| 5 | Clear JSON errors | Done | `app/errors.py`, `tests/test_contract.py::test_every_error_is_the_documented_json_shape`, error table in `README.md` | |
| 6 | Configuration from environment | Done | `app/config.py`, `.env.example`, settings table in `README.md` | |
| 7 | Files in/out, S3 storage | Done (inputs: upload or URL) | Certification evidence is written to RustFS and returned as presigned download links (`app/evidence.py`; `evidence/manual-demos/certification/01-s3-download-link-ghost-run.png`) | |
| 8 | JSON logs + `/health` + `/ready` | Done | `app/logging_setup.py`, `tests/test_foundation.py` | |
| 9 | Output manifest per result | Done | `app/output_manifest.py`; in every certification record, publish result, import result and rules proposal: versions, models/providers, seed, settings, timings, cost, warnings, licences, AI-generated label | |
| 10 | Batch runner CLI | Done | `app/cli.py` runs a folder, a JSON list or a JSONL file against the API (`POST /api/v1/publish`) and writes everything to one folder; `tests/test_cli.py`; outputs in `test-pack/batch-run/` and `test-pack/batch-run-rerun/` | |

## Levels

**Core:** each Core connector passes full certification (publish, live, status, delete) with stored evidence: Done. Errors mapped for expired token, rate limit and rejected content: Done (every connector has tests for these).

**Advanced:**
- Done. CSV import: validate, normalise, de-duplicate (file, catalogue, and by final address plus platform after redirects), reachability, enrichment (member count for Lemmy and Discourse, rules page link, platform, category, language, audience relevance), row-by-row report; the CSV may be uploaded or given as an address. **1,000 rows in 19 s** (limit 300 s). `app/csv_import.py`, `tests/test_csv_import.py`, outputs in `test-pack/csv-import/sample-1000/`. Limit: all 1,000 test sites sit on one host, so the one-request-per-host politeness rule serialises them; a real list spread over many hosts would be faster, but that is not measured.
- Done. Rules capture: an AI reads a rules page, proposes rules with quotes, **a person confirms** before use: `app/rules_capture.py`, `app/api/rules_routes.py`, `tests/test_rules_capture.py`, `tests/test_gemini_extractor.py`. Quotes are verified against the page (an invented rule is dropped), nothing applies until a named person confirms, loosening needs an extra acknowledgement. **Run live with Google Gemini (free tier) on 15 real communities:** 6 rules verified across 5 communities, 0 dropped, `catalogue/real/ai_rules/` (entry "AI rules capture, live run" below). Limits: only 15 of 152 communities; the default reader is the offline keyword reader; the Claude adapter is tested against a fake API only.
- Done. Relevance score 1 to 5 against IncuBrix's audience (AI, video, marketing, SaaS, creators, founders) with a reason for each destination: `app/audience.py`, shown as `relevance_score` and `relevance_reason` on every destination record and in the import report, `tests/test_catalogue_record.py`. Rule-based and repeatable. (A second score, `app/relevance.py` / `POST /api/v1/relevance`, rates how well one post suits one destination.)
- Done. Tailoring: `app/tailoring.py`, `POST /api/v1/tailor`, `tests/test_tailoring.py`: word-boundary truncation, link policy, mention style, tag style or hashtags, canonical URL, AI disclosure never cut, language warning; a test proves the tailored output passes every connector's own validate().
- Done. Three more connectors: **MRSS feed** (`app/connectors/feed.py`, `app/feeds.py`, `app/api/feed_routes.py`: a real feed host, valid Media RSS), **Hashnode** (`app/connectors/hashnode.py`, GraphQL, errors read from the body) and **Kit** (`app/connectors/kit.py`, API v4 broadcasts, drafts only so it can never email subscribers). Tests: `tests/test_new_connectors.py`. Limit: Hashnode and Kit are built against the documented APIs and a mock, not verified against real accounts; the feed is verified end to end because we host it.

**Advanced demo against the running service:** `python -m scripts.demo_advanced` -> `test-pack/advanced-demos/transcript.md` (tailoring, relevance with explanations, rules capture with quotes, human confirmation, a second confirmation refused).

**Outstanding:**
- Done. Recommender (ranks by content fit, audience relevance, rules and track record, with a reason) with measured top-10 precision: `app/recommender.py`, `POST /api/v1/recommend`, `scripts/eval_recommender.py`, labelled set `samples/recommender/labelled_set.json`, results `test-pack/recommender/eval.md`. **precision@10: recommender 96.7%, plain relevance score 82.0%, random 9.0%; without the synonym table 94.3%.** Limit: the set is synthetic (300 generated destinations, 30 generated posts) and the synonym table and the set were written by the same author, so the figure is optimistic.
- Done. `fetch_metrics` on 5 connectors: Lemmy, Discourse, Dev.to, Hashnode, Kit (tests in `tests/test_discourse_devto_connectors.py`, `tests/test_new_connectors.py`, `tests/test_lemmy_connector.py`).
- Done. Recertification scheduler with auto-demotion and an alert (`GET /api/v1/alerts`, optional signed webhook); destinations are always retested before 30 days (a larger setting is lowered): `app/recertify.py`, `app/api/recert_routes.py`, `tests/test_recertify.py` (fake clock). Certified destinations are rechecked after 7 days, demoted ones after a 1-day cooldown (a pass promotes them back), needs_attention after 1 hour. A demotion blocks publishing (`DEST_SPEC_VIOLATION`, rule `destination_demoted`) and removes the destination from recommendations. Off by default (`RECERT_INTERVAL_S=0`); run by hand with `POST /api/v1/recertify`. Demo: `python -m scripts.demo_recertification` -> `test-pack/recertification/transcript.md`.
- **Partial.** Real catalogue: **152 destinations listed** in `catalogue/real/real_catalogue.csv` (the destination record), with `import_report.csv`, `rules_evidence.json` and `SUMMARY.md`, built by `python -m scripts.build_real_catalogue` from `samples/research/*.json` (168 candidates, 16 duplicates removed). 137 answered HTTP 200, 10 blocked the importer (403), 5 forbid automated visits (robots.txt). Rules were proposed (unconfirmed) for only 13 destinations: 5 by the Gemini AI reader from the live page, 8 by the keyword reader from stored quotes; the rest hold the conservative default, so this item is marked Partial in the form. Geo was not researched. All entries are catalogue only (never posted to).

## Hard cases (tab `6_Hard_cases`): each has a test and a saved output

| Hard case | Status | Evidence / test |
|---|---|---|
| Publish times out, retried with same key | Done | `test_timeout_after_create_then_retry_does_not_duplicate`; live service: `test-pack/failure-demos/1-timeout-then-retry.json`; `tests/test_hard_cases.py` |
| 429 with reset time | Done | `test_http_429_with_retry_after`; live service: `test-pack/failure-demos/2-rate-limit.json` |
| Expired token | Done | `test_wrong_password_is_auth_expired`; `test-pack/failure-demos/4-no-credentials.json` |
| Content rejected by platform | Done | `test_platform_refusal_is_content_rejected`, `test_discourse_content_rules_become_a_rejection` |
| Title/body too long | Done | `test_validate_reports_field_level_violations`, `tests/test_hard_cases.py` |
| Media the destination does not accept | Done | `tests/test_media.py`, `tests/test_hard_cases.py::test_media_the_destination_does_not_accept_is_refused_and_says_which_item` (field `media[1]`, nothing sent) |
| Second post inside frequency cap | Done | `test_second_post_inside_the_cap_is_blocked_with_a_reason` |
| Promo post to a no-promo community | Done | `test_promo_to_no_promo_destination_never_reaches_the_platform`; `test-pack/batch-run/04-blocked-no-promo.result.json` |
| Platform renames a field: certification fails, destination demoted | Done | `test_a_renamed_field_fails_certification_demotes_and_leaves_no_stray_post`; live service: `test-pack/failure-demos/3-renamed-field.json` |
| CSV with duplicates, bad URLs, mixed languages | Done | `test_every_kind_of_row_gets_the_right_status_and_reason`; 1,000-row run: 108 duplicates, 60 invalid, 70 unreachable, language-mismatch warnings (`test-pack/csv-import/sample-1000/report.csv`) |
| Partner revokes permission | Done | `test_a_partner_who_revokes_permission_is_an_auth_error_not_a_retry` (HTTP 410) |
| Non-English community | Done | `tests/test_hard_cases.py::test_a_non_english_community_takes_non_english_content_intact_and_warns_about_english_content` |
| Destination requires AI disclosure | Done | `tests/test_rules.py::test_ai_disclosure_required`, `tests/test_hard_cases.py` |

## Metrics (tab `5_Metrics`)

- Certification: 9 destinations (9 connectors) certified in one `certify_batch` through the running service on the fresh-clone run, 9 passed, 0 failed; mean time of a passed run: Lemmy 0.26 s, Ghost 0.26 s, the mock-based connectors 0.01 to 0.03 s; evidence in `evidence/certification/`.
- Reliability: 200 of 200 simulated publishes; 20 of 20 full certification cycles on each of the real local Lemmy and Ghost; **0 duplicates in 120 retry cases**: `test-pack/reliability/report.md`.
- Rule safety: 0 missed of 12,317 violations, 0 false blocks of 3,240 legal posts: `test-pack/rule-safety/report.md`.
- Catalogue (Advanced): 1,000 rows -> 762 accepted, 108 duplicates, 60 invalid, 70 unreachable, in 19 s (54.5 rows per second, 32 workers).
- Recommendation (Outstanding): precision@10 96.7% on a synthetic labelled set (see the limit above): `test-pack/recommender/eval.md`.
- Cost: no paid model or API in the default path; the Gemini AI step used the free tier (20,108 tokens in, 388 out).

## Manual captures (screenshots taken by hand by the developer)

Each is described in `evidence/manual-demos/README.md`.

- Lemmy UI showing the batch-runner post: `evidence/manual-demos/lemmy/01-batch-runner-post-live.png`
- Ghost post live and then deleted (404): `evidence/manual-demos/ghost/01-published-live-1233.png`, `02-deleted-404-1234.png`
- Certification summary opened from a presigned S3 link: `evidence/manual-demos/certification/01-s3-download-link-ghost-run.png`
- `python -m pytest` finishing green (an earlier, smaller suite; the current count is above): `evidence/manual-demos/tests/01-pytest-208-passed.png`
- `python -m scripts.demo_failures` output: `evidence/manual-demos/failure-demos/01-demo-failures-output.png`
- The public demo: all live checks passing and the Render service live: `evidence/manual-demos/live-demo/`

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

### Public demo re-checked on the final code (24 Oct 2026)
Render auto-deployed commit d27eadc (49.9 s). `python -m scripts.check_live_demo https://incubrix-demo.onrender.com` (token typed only on the developer's machine) passed all 10 checks: health 0.78 s, 401 without a token, 7 destinations, publish, same-key replay, outcome unknown (503) on a late answer, retry succeeded, exactly 1 post on the platform (0 duplicates), catalogue export.

### AI rules capture, live run (24 Oct 2026)
- `python -m scripts.live_rules_capture --extractor=gemini --limit=15` with `RULES_MODEL=gemini-3.8-flash` (free tier; the key was set only in the developer's terminal). Output: `catalogue/real/ai_rules/results.json`, `SUMMARY.md`, `progress.jsonl`.
- Result: 15 communities read (13 from the live rules page, 2 from stored verbatim quotes), 0 failed calls, 5 communities with at least one verified rule, 6 verified rules, 0 of 6 proposals dropped, 20,108 tokens in and 388 out, 461 s (calls paced 6 s apart), cost $0 on the free tier.
- Problems found on the way: `gemini-2.5-flash` returned 404 ("no longer available to new users"), so the model was changed to `gemini-3.8-flash`; the first full run hit the free quota and every later call got HTTP 429, so the script was made resumable, paced, and to stop cleanly when the quota is gone; a read timeout at 90 s became 240 s with retries.
- Limits: 15 of 152 communities, proposals are unconfirmed, the page text read by the model is not stored (the quote and the page address are).
- `python -m scripts.build_real_catalogue` was re-run afterwards: 5 communities now carry AI-proposed rules (marked `proposed_by_ai_unconfirmed`), 8 carry rules from the offline keyword reader, the rest keep the conservative default.
