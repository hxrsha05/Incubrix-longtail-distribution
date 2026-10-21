# EVIDENCE_LOG.md — what is done, where the proof is, what is still missing

> The Excel form says: *"A claim without evidence scores 0. A claim that turns out to be false disqualifies the whole
> submission."* So: only tick **Yes** when the proof exists and I have seen it work.
> **Status key:** Done = done and proven; Partial = partly done; Not started; Unverified = written but not yet proven

Last updated: 2026-10-21. Unit tests: **76 passing** (`python -m pytest`, run from this folder). Live tests against the local Lemmy: **9 passing** (opt-in, see `tests/live/test_lemmy_live.py`).

## Must-haves (all 9 must pass)

| # | Must-have | Status | Evidence now | Still missing |
|---|---|---|---|---|
| 1 | Exact connector contract | Partial | `app/connectors/base.py`, `app/api/connector_routes.py`, `tests/test_connector_framework.py` | Contract test for every method on every connector; `manifest` check per connector |
| 2 | Six Core connectors certified | Partial | Lemmy: 15 unit tests (`tests/test_lemmy_connector.py`) and 9 live tests against the real local Lemmy (`tests/live/test_lemmy_live.py`): publish, visible in web UI, status, delete, reconcile, limits, errors | Certification harness and stored evidence; Ghost, Discourse, Dev.to, signed webhook, assisted connector |
| 3 | Configuration, not code | Partial | Per-destination spec override: `test_destination_spec_overrides_manifest_default` | Catalogue API so a new forum is one row; live "add an instance" demo |
| 4 | Rules checked before every post | Done | `tests/test_rules.py`, `tests/test_idempotency.py` (cap, promo, links, AI disclosure, fail-closed) | Show it through the live demo; rule test set for "caught vs missed" metric |
| 5 | Never spam, never cheat | Partial | 429 + reset-time mapping tested; own-instances-only design | Certification harness, auto-delete within 10 minutes (also on failure), consecutive-failure stop, written proof from logs |
| 6 | Safe retries | Done (unit) | `tests/test_idempotency.py::test_timeout_after_create_then_retry_does_not_duplicate` and others | Forced-timeout run against the live demo; screenshot/log for the form |
| 7 | Secrets protected + simulation mode | Partial | Redaction: `app/logging_setup.py`, `tests/test_foundation.py` | Simulation mode with mock platforms; search repo + logs for tokens and save the result |
| 8 | Local test instances (compose) | Partial | `docker compose up` starts all services here; `scripts/seed_local.py` creates the Lemmy community, the Ghost owner and Ghost API key, and is safe to re-run | Run on a clean machine; Ghost live test; mock servers for the rest |
| 9 | Integration Standard + live demo | Not started | see below | Deploy live demo; keep it up until results |

## Integration Standard (2 marks each)

| # | Point | Status | Evidence now | Still missing |
|---|---|---|---|---|
| 1 | One-command start and test | Partial | `docker-compose.yml`, `.env.example`; stack starts locally | Clean-machine run; one command that runs all tests with no keys |
| 2 | `openapi.yaml` matching the service | Not started | FastAPI generates `/api/v1/openapi.json` | Commit `openapi.yaml`; test that it matches the running service |
| 3 | Background jobs (202, status, HMAC callback) | Done | `app/jobs.py`, `tests/test_jobs.py` | Real job kinds (certification, import) |
| 4 | Idempotency-Key on every POST | Done | `tests/test_idempotency.py`, `tests/test_jobs.py` | Check *every* POST route honours it |
| 5 | Clear JSON errors | Done (code) | `app/errors.py` | List all codes in README |
| 6 | Configuration from environment | Done | `app/config.py`, `.env.example` | Keep `.env.example` complete as settings are added |
| 7 | Files in/out, S3 storage | Partial | RustFS in compose | Service must actually write outputs to S3 and return download links |
| 8 | JSON logs + `/health` + `/ready` | Done | `app/logging_setup.py`, `tests/test_foundation.py` | — |
| 9 | Output manifest per result | Not started | — | Manifest with versions, settings, timings, cost, warnings, licences, AI-generated labels |
| 10 | Batch runner CLI | Not started | — | CLI that runs a folder of inputs and writes results to one folder |

## Levels

**Core:** each Core connector passes full certification (publish → live → status → delete) with stored evidence Not started ·
errors mapped for expired token / rate limit / rejected content Partial (Lemmy + framework tested, others pending)

**Advanced — ALL five required, or the level scores 0 (and screening cannot reach 60):**
- [ ] CSV import: validate, de-duplicate, reachability, enrichment, **1,000 rows < 5 min**, row-by-row error report
- [ ] Rules capture: AI reads a rules page → proposes rules → **a person confirms** before use
- [ ] Relevance score 1–5 with a short explanation
- [ ] Tailoring: title/text per destination, canonical URL, truncate at word boundary, hashtag/mention style
- [ ] Three more connectors (suggested: MRSS feed, Hashnode, Kit/Mailchimp)

**Outstanding (stretch):** recommender with top-10 precision on my labelled set · `fetch_metrics` on ≥3 connectors
(Lemmy has it) · recertification scheduler with auto-demotion · ≥150 real destinations researched

## Hard cases (tab `6_Hard_cases`) — each needs a test and a saved output

| Hard case | Status | Evidence / test |
|---|---|---|
| Publish times out, retried with same key | Done (unit) | `test_timeout_after_create_then_retry_does_not_duplicate` |
| 429 with reset time | Done (unit) | `test_http_429_with_retry_after` |
| Expired token | Done (unit) | `test_wrong_password_is_auth_expired` |
| Content rejected by platform | Done (unit) | `test_platform_refusal_is_content_rejected` |
| Title/body too long | Done (unit) | `test_validate_reports_field_level_violations` |
| Media the destination does not accept | Not started | |
| Second post inside frequency cap | Done (unit) | `test_second_post_inside_the_cap_is_blocked_with_a_reason` |
| Promo post to a no-promo community | Done (unit) | `test_promo_to_no_promo_destination_never_reaches_the_platform` |
| Platform renames a field → certification fails, destination demoted | Not started | needs certification harness + demotion |
| CSV with duplicates, bad URLs, mixed languages | Not started | needs CSV import |
| Partner revokes permission | Not started | |
| Non-English community | Not started | |
| Destination requires AI disclosure | Done (unit) | `tests/test_rules.py::test_ai_disclosure_required` |

## Metrics to measure (tab `5_Metrics`)

- [ ] Certification: destinations certified per connector, time per run, evidence links
- [ ] Reliability: publish success rate (simulation + local); **duplicates under retry = 0**
- [ ] Rule safety: violations caught vs missed on a rule test set (any miss is serious)
- [ ] Catalogue (Advanced): rows imported, duplicates and errors found, time for 1,000 rows
- [ ] Recommendation (Outstanding): top-10 precision on my labelled set
- [ ] Cost: any API/model cost per 1,000 publications

## Deliverables checklist

- [ ] Private GitHub repo, `admin@incubrix.com` invited, real commit history (commit small and often)
- [ ] Live demo URL + token + cold-start time, rate-limited, **up until results are announced**
- [ ] README (architecture diagram, setup in under 10 commands, run tests + batch runner, all settings, error-code list)
- [ ] `openapi.yaml`
- [ ] `REPORT.md` (2–4 pages: approaches, what works, what doesn't and why, numbers, cost per unit, next steps)
- [ ] `SOURCES.md` and `AI_USE.md` (all licence links verified)
- [ ] Test pack: all inputs, all outputs **including failures**, logs, index linking each claim to a file
- [ ] Certification pack: per destination — run record, live URL/API response, screenshot, deletion confirmation
- [ ] Catalogue files: CSVs and import reports (+ labelled set if Outstanding)
- [ ] Demo video ≤ 8 min, **unedited**: one clean run · one failure and how it's handled · tests running · one design decision explained in code
- [ ] Excel form `Assessment4_YourName.xlsx`: all tabs complete, every claim linked, **Declaration signed**
- [ ] Final Submission Form submitted **and** the Excel attached to a reply to the invitation email (do both until told otherwise)

## Final-day safety checks

- [ ] Fresh clone → `docker compose up` works on a clean setup
- [ ] Search repo, logs, screenshots and the Excel for tokens/passwords/keys
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
