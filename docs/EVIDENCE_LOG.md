# EVIDENCE_LOG.md — what is done, where the proof is, what is still missing

> The Excel form says: *"A claim without evidence scores 0. A claim that turns out to be false disqualifies the whole
> submission."* So: only tick **Yes** when the proof exists and I have seen it work.
> **Status key:** Done = done and proven; Partial = partly done; Not started; Unverified = written but not yet proven

Last updated: 2026-10-22. Unit and integration tests: **174 passing** (`python -m pytest`, run from this folder, no keys needed). Live tests against the real local Lemmy and Ghost: **18 passing** (opt-in, see `tests/live/`).

## Must-haves (all 9 must pass)

| # | Must-have | Status | Evidence now | Still missing |
|---|---|---|---|---|
| 1 | Exact connector contract | Done | `app/connectors/base.py`, `app/api/connector_routes.py`, `tests/test_connector_framework.py`, `tests/test_contract.py` (all 7 methods on all 6 connectors through the HTTP door), `openapi.yaml` + `tests/test_openapi.py` | — |
| 2 | Six Core connectors certified | Done | Real certification runs of Lemmy and Ghost (real local instances) and Discourse, Dev.to, signed webhook, assisted (mock platforms): `evidence/certification/*/run-*.txt|json`, produced by `certify_batch` through the running service (6 of 6 passed). Unit tests per connector in `tests/`, live tests in `tests/live/` | Screenshots of a live Lemmy post and a Ghost post (manual, see below); Discourse and Dev.to are verified against a mock, not the real platforms (stated honestly in the form) |
| 3 | Configuration, not code | Done | Catalogue API (`app/api/catalogue_routes.py`, `tests/test_certification.py::test_catalogue_api_...`): a new destination is one POST; per-destination spec override test | Live "add an instance" demo in the video |
| 4 | Rules checked before every post | Done | `tests/test_rules.py`, `tests/test_idempotency.py`; batch runner shows a blocked promo post: `test-pack/batch-run/04-blocked-no-promo.result.json` | Rule test set for the "caught vs missed" metric |
| 5 | Never spam, never cheat | Done | 429 + reset time (`test-pack/failure-demos/2-rate-limit.json`), certification waits out a 429 with a cap, stops after 3 consecutive failures on a connector, test posts deleted (also on failure, within 10 minutes: `tests/test_certification.py`), own local instances only | Written proof from logs for the form |
| 6 | Safe retries | Done | `tests/test_idempotency.py::test_timeout_after_create_then_retry_does_not_duplicate`; **live-service run**: `test-pack/failure-demos/transcript.md` case 1 (timeout after the platform created the post, retry with the same key, 0 duplicates); batch re-run replays instead of re-posting (`test-pack/batch-run-rerun/`) | Screenshot of the transcript for the form |
| 7 | Secrets protected + simulation mode | Done | Redaction in `app/logging_setup.py` + tests; secret never in errors (webhook, Discourse, Dev.to tests); `evidence/secret-scan.txt` (repo files and service logs: CLEAN); simulation mode (`README.md`, `app/credentials.py`) | Re-run `python -m scripts.secret_scan --docker-logs` just before submitting |
| 8 | Local test instances (compose) | Partial | `docker compose up` starts Lemmy, Ghost, Postgres, S3, API, mock platforms and the seed job; seed is safe to re-run | Run on a clean machine (fresh clone) |
| 9 | Integration Standard + live demo | Partial | see below | Deploy live demo; keep it up until results |

## Integration Standard (2 marks each)

| # | Point | Status | Evidence now | Still missing |
|---|---|---|---|---|
| 1 | One-command start and test | Done | `docker compose up -d --build`; `python -m pytest` (no keys) | Clean-machine run |
| 2 | `openapi.yaml` matching the service | Done | `openapi.yaml`, `tests/test_openapi.py` fails when stale | — |
| 3 | Background jobs (202, status, HMAC callback) | Done | `app/jobs.py`, `tests/test_jobs.py`; real kinds `certify`, `certify_batch` | `import` job kind (Advanced) |
| 4 | Idempotency-Key on every POST | Done | `tests/test_idempotency.py`, `tests/test_jobs.py`, destination POST (`tests/test_certification.py`), certify POST | Add the import POST when built |
| 5 | Clear JSON errors | Done | `app/errors.py`, `tests/test_contract.py::test_every_error_is_the_documented_json_shape`, error table in `README.md` | — |
| 6 | Configuration from environment | Done | `app/config.py`, `.env.example`, settings table in `README.md` | — |
| 7 | Files in/out, S3 storage | Done | Certification evidence is written to RustFS and returned as presigned download links (`app/evidence.py`; real run through compose: `certify_batch` result links) | Open one link in the browser for the video |
| 8 | JSON logs + `/health` + `/ready` | Done | `app/logging_setup.py`, `tests/test_foundation.py` | — |
| 9 | Output manifest per result | Done | `app/output_manifest.py`; present in every certification record and every batch-runner result (versions, settings, timings, cost, warnings, licences, AI-generated label) | Add to import results |
| 10 | Batch runner CLI | Done | `app/cli.py`, `tests/test_cli.py`; outputs in `test-pack/batch-run/` and `test-pack/batch-run-rerun/` | — |

## Levels

**Core:** each Core connector passes full certification (publish → live → status → delete) with stored evidence **Done** ·
errors mapped for expired token / rate limit / rejected content **Done** (every connector has tests for these)

**Advanced — ALL five required, or the level scores 0 (and screening cannot reach 60):**
- [ ] CSV import: validate, de-duplicate, reachability, enrichment, **1,000 rows < 5 min**, row-by-row error report
- [ ] Rules capture: AI reads a rules page → proposes rules → **a person confirms** before use
- [ ] Relevance score 1–5 with a short explanation
- [ ] Tailoring: title/text per destination, canonical URL, truncate at word boundary, hashtag/mention style
- [ ] Three more connectors (suggested: MRSS feed, Hashnode, Kit/Mailchimp)

**Outstanding (stretch):** recommender with top-10 precision on my labelled set · `fetch_metrics` on ≥3 connectors
(**done**: Lemmy, Discourse, Dev.to) · recertification scheduler with auto-demotion (demotion on a failed run is done; the scheduler is not) · ≥150 real destinations researched

## Hard cases (tab `6_Hard_cases`) — each needs a test and a saved output

| Hard case | Status | Evidence / test |
|---|---|---|
| Publish times out, retried with same key | Done | `test_timeout_after_create_then_retry_does_not_duplicate`; live service: `test-pack/failure-demos/1-timeout-then-retry.json` |
| 429 with reset time | Done | `test_http_429_with_retry_after`; live service: `test-pack/failure-demos/2-rate-limit.json` |
| Expired token | Done | `test_wrong_password_is_auth_expired`; `test-pack/failure-demos/4-no-credentials.json` |
| Content rejected by platform | Done | `test_platform_refusal_is_content_rejected`, `test_discourse_content_rules_become_a_rejection` |
| Title/body too long | Done | `test_validate_reports_field_level_violations` |
| Media the destination does not accept | Partial | content-type check exists in `validate`; add an explicit test |
| Second post inside frequency cap | Done | `test_second_post_inside_the_cap_is_blocked_with_a_reason` |
| Promo post to a no-promo community | Done | `test_promo_to_no_promo_destination_never_reaches_the_platform`; `test-pack/batch-run/04-blocked-no-promo.result.json` |
| Platform renames a field → certification fails, destination demoted | Done | `test_a_renamed_field_fails_certification_demotes_and_leaves_no_stray_post`; live service: `test-pack/failure-demos/3-renamed-field.json` |
| CSV with duplicates, bad URLs, mixed languages | Not started | needs CSV import |
| Partner revokes permission | Done | `test_a_partner_who_revokes_permission_is_an_auth_error_not_a_retry` (HTTP 410) |
| Non-English community | Not started | add language to a catalogue row (field exists) and a non-English tailoring test |
| Destination requires AI disclosure | Done | `tests/test_rules.py::test_ai_disclosure_required` |

## Metrics to measure (tab `5_Metrics`)

- [x] Certification: 6 destinations certified in one batch (Lemmy 0.6 s, Ghost 1.5 s, others under 0.05 s); evidence links in `evidence/certification/`
- [ ] Reliability: publish success rate (simulation + local); **duplicates under retry = 0** (shown once live; measure over many runs)
- [ ] Rule safety: violations caught vs missed on a rule test set (any miss is serious)
- [ ] Catalogue (Advanced): rows imported, duplicates and errors found, time for 1,000 rows
- [ ] Recommendation (Outstanding): top-10 precision on my labelled set
- [x] Cost: no paid API or model is called so far (manifest records 0); update when the AI features are added

## Deliverables checklist

- [x] Private GitHub repo, `admin@incubrix.com` invited, real commit history (commit small and often)
- [ ] Live demo URL + token + cold-start time, rate-limited, **up until results are announced**
- [x] README (architecture diagram, setup, run tests + batch runner, all settings, error-code list) — re-check the setup steps on a clean machine
- [x] `openapi.yaml`
- [ ] `REPORT.md` (2–4 pages: approaches, what works, what doesn't and why, numbers, cost per unit, next steps)
- [ ] `SOURCES.md` and `AI_USE.md` (AI_USE kept current; all licence links still to verify)
- [ ] Test pack: all inputs, all outputs **including failures**, logs, index linking each claim to a file (inputs: `samples/inputs/`; outputs: `test-pack/`; index still to write)
- [x] Certification pack: per destination — run record (`evidence/certification/<destination>/run-*.json|txt`); live URL and deletion confirmation are inside each record; screenshots are manual (below)
- [ ] Catalogue files: CSVs and import reports (+ labelled set if Outstanding)
- [ ] Demo video ≤ 8 min, **unedited**: one clean run · one failure and how it's handled · tests running · one design decision explained in code
- [ ] Excel form `Assessment4_YourName.xlsx`: all tabs complete, every claim linked, **Declaration signed**
- [ ] Final Submission Form submitted **and** the Excel attached to a reply to the invitation email (do both until told otherwise)

## Manual captures still wanted (screenshots the AI cannot take)

- [x] Lemmy UI showing the batch-runner post: `evidence/manual-demos/lemmy/01-batch-runner-post-live.png`
- [ ] The certification summary text opened from a presigned S3 link in the browser (link is in the `certify_batch` job result)
- [ ] Terminal with `python -m pytest` finishing green
- [ ] Terminal with `python -m scripts.demo_failures` output

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
