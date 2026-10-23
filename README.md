# IncuBrix Long-Tail Distribution (assessment PS4)

A service that posts content to many small destinations (forums, blogs, partner sites) safely: one connector per
platform type, one catalogue row per destination, rules checked before every post, retries that never duplicate, and
a certification run that proves each destination works and saves the evidence.

## Architecture

```
   caller (IncuBrix Connector Runtime, or you)
        |  POST /connector/v1/{connector}/{method}        POST /api/v1/destinations, /certify, /jobs
        v
+--------------------------- FastAPI service (api) ------------------------------+
|  publish pipeline:  1. idempotency store  ->  2. rules engine  ->  3. connector |
|                                                                                  |
|  connectors:  lemmy ghost discourse devto webhook(signed) assisted hashnode kit feed |
|  catalogue:   destinations table (a new forum = a new row)                       |
|  certification: publish test post -> live? -> status -> delete -> gone?          |
|                 always cleans up, saves evidence, demotes on a changed platform  |
+--------+-------------------+-----------------------+-----------------------------+
         |                   |                       |
     Postgres             RustFS (S3)          platforms: real local Lemmy + Ghost,
  (state, catalogue,     (evidence files,      mock Discourse / Dev.to / partner site
   certification runs)    download links)      (mock-platforms, with failure switches)
```

Key ideas (details in the code comments):

- **Connector vs destination.** A connector knows one platform type (its API, limits, error mapping, in a YAML manifest).
  A destination is one forum: a catalogue row with its address, community, limits and rules. Adding a forum is data, not code.
- **Safe retries.** The same Idempotency-Key never posts twice. After a timeout the service asks the platform whether the
  post exists (`reconcile`) instead of guessing.
- **Rules first, fail closed.** Frequency cap, self-promotion, links, AI disclosure and automation rules are checked
  before anything leaves the service. A destination with missing rules is refused.
- **Certification** deletes its test post within 10 minutes, also when a step fails.

## Set up (7 commands)

```
git clone https://github.com/hxrsha05/Incubrix-longtail-distribution.git
cd Incubrix-longtail-distribution
copy .env.example .env                 # (cp on Linux/Mac). The defaults work for local testing, no keys needed
docker compose up -d --build           # first start downloads images and takes a few minutes
docker compose logs -f seed            # wait for "wrote /seed/connections.json", then Ctrl+C
python -m pip install -r requirements-dev.txt
python -m scripts.seed_catalogue       # fills the catalogue with the six local test destinations
```

Then open http://localhost:8000/docs (API), http://localhost:1234 (Lemmy), http://localhost:2368 (Ghost, add `127.0.0.1 ghost.localhost` to your hosts file if your browser cannot open it), http://localhost:9001 (S3 console).

## Run things

| What | Command |
|---|---|
| **All tests, no keys, no internet** | `python -m pytest` |
| Live tests against the running Lemmy and Ghost | see `tests/live/` (they switch on with `LEMMY_LIVE_URL` / `GHOST_LIVE_URL`) |
| Certify every destination in the catalogue | `curl -X POST localhost:8000/api/v1/jobs -H "Content-Type: application/json" -d "{\"kind\":\"certify_batch\",\"params\":{}}"` |
| Batch runner: a folder or file of inputs sent through the API, results in one folder | `python -m app.cli run --input samples/inputs --output test-pack/batch-run --api http://localhost:8000 [--token T]` |
| Batch certification through the API | `python -m app.cli certify --output test-pack/batch-certification [--connector discourse]` |
| Publish to a catalogue destination (the service finds the credentials) | `curl -X POST localhost:8000/api/v1/publish -H "Idempotency-Key: k1" -H "Content-Type: application/json" -d '{"destination_id":"mock-discourse-forum","rendition":{"title":"...","body":"..."}}'` |
| The catalogue as destination records (CSV or JSON) | `curl localhost:8000/api/v1/catalogue/export?format=csv` |
| Import a CSV of candidate destinations (job; `?commit=false` for a dry run) | `curl -X POST localhost:8000/api/v1/imports -H "Content-Type: text/csv" --data-binary @samples/catalogue/sample-1000.csv` |
| The 1,000-row import demo, with timing and saved report | `python -m scripts.import_demo` |
| Regenerate `openapi.yaml` (a test fails if it is stale) | `python -m scripts.export_openapi` |
| Scan the repo and service logs for leaked secrets | `python -m scripts.secret_scan --docker-logs` |

A batch input is a folder of `*.json` files, one `.json` file holding a list, or a `.jsonl` file:
`{"destination_id": "mock-discourse-forum", "rendition": {"title": "...", "body": "..."}}`. Each goes to `POST /api/v1/publish` with an
Idempotency-Key made from the file name and a hash of its content (so an unchanged file is a replay and an edited file is a new post). Results: one
`<name>.result.json` per input plus `summary.json`; each result has an output manifest (versions, models, seed, settings, timings, cost, warnings, licences,
AI-generated label).

## Recommender and recertification

`POST /api/v1/recommend` suggests the best destinations for a piece of content (rare words weigh more, endings and obvious synonyms are folded together, destinations that would refuse the content or that are demoted are left out); each suggestion carries a 1 to 5 score and the reason. Quality is measured, not claimed: `python -m scripts.eval_recommender` reports precision@10 on a labelled set (`test-pack/recommender/eval.md`; the set is synthetic, read its caveats).

Destinations are re-checked on a schedule (`RECERT_INTERVAL_S`, off by default; or `POST /api/v1/recertify`, `GET /api/v1/recertification`). A failed re-check **demotes** a destination: publishing to it is refused and it is not recommended until a later check passes. Uncertified destinations are never re-checked automatically, because certification publishes a test post.

## The nine connectors

| Connector | What it posts to | Notes |
|---|---|---|
| lemmy | a Lemmy community | real local instance in compose |
| ghost | a Ghost blog | real local instance in compose |
| discourse | a Discourse forum | mock server (modelled, not verified against a real forum) |
| devto | Dev.to | mock; Dev.to has no delete, so delete = unpublish |
| webhook | a partner site | every call signed with HMAC-SHA256 (X-IncuBrix-Signature) |
| assisted | any site without an API | builds an assist pack for a person; never posts |
| hashnode | a Hashnode blog (GraphQL) | mock; errors arrive inside HTTP 200 bodies and are mapped |
| kit | a Kit newsletter | mock; drafts only, never sends |
| feed | an MRSS feed we host (`/feeds/{id}.xml`) | verified end to end; partners poll the feed |

## AI rules capture (a person confirms)

`POST /api/v1/destinations/{id}/rules/capture` reads a community's rules page (`url`) or pasted text and creates a **pending proposal**: for each rule it found, a value and a quote from the page. Quotes are checked against the page, so an invented rule is dropped. Nothing changes until a person calls `POST /api/v1/rule-proposals/{id}/confirm` with their name (they may edit values first); making a destination looser needs `acknowledge_loosening=true`. Until then the destination keeps its conservative rules. `RULES_EXTRACTOR=mock` (default) is an offline keyword reader; `RULES_EXTRACTOR=anthropic` with `ANTHROPIC_API_KEY` uses Claude.

## The destination record, levels and alerts

Every catalogue row carries its **destination record** (`record` in `GET /api/v1/destinations`, and `GET /api/v1/catalogue/export` as CSV or JSON):
name, url, platform_key, surface, category, audience_tags, language, geo, est_audience, rules_url, frequency_cap, self_promo_allowed, link_policy,
disclosure_required, automation_allowed, connector_key, relevance_score (1 to 5 against IncuBrix's audience: AI, video, marketing, SaaS, creators, founders),
relevance_reason, level and last_certified_at. **Levels:** L0 catalogued only (anything not certified), L1 assisted, L2 bridged or feed, L3 native API,
L4 native plus metrics. A destination counts for a level only once certified. Demotions and recoveries raise **alerts**
(`GET /api/v1/alerts`, acknowledge with `POST /api/v1/alerts/{id}/acknowledge`, optional signed webhook).

## How to trigger each hard case on a running demo

The fake platforms (port 9100, `mock-platforms`) have failure switches: `POST /__control/fail` with `{"platform": "discourse", "op": "create", "kind": "..."}`
where kind is `status` (with `status` and `retry_after`), `timeout_before`, `timeout_after` (the post is made, the answer is late; `delay_s`) or `rename_field`
(`rename: {"topic_id": "topic_identifier"}`); `POST /__control/reset` clears them (add `X-Mock-Token` if the demo sets one). Then call `POST /api/v1/publish`
(or `POST /connector/v1/{connector}/publish`) and read the error. `tests/test_hard_cases.py` does every case from the brief this way, end to end.

## Tailoring and relevance

`POST /api/v1/tailor` fits content to one destination and lists every change (word-boundary shortening, link policy, mention and tag style, canonical URL, AI disclosure that is never cut, language warning). `POST /api/v1/relevance` scores the catalogue 1 to 5 for a piece of content and explains each score. The batch runner tailors by default (`--no-tailor` turns it off).

## CSV import

Columns: `name`, `url` (required); `language`, `topics` (a;b;c), `connector_key`, `community` (optional). Every row is validated,
normalised, de-duplicated (against the file and the catalogue), checked for reachability and enriched (title, language,
platform). Accepted rows become catalogue rows with conservative rules (no self-promotion, one link, disclosure, one post a
week) until a person confirms better ones. A site on Discourse, Ghost or Lemmy gets that connector; any other site becomes an
"assisted" destination. The result is a row-by-row `report.csv` and `report.json` with download links.

## Simulation mode

`SIMULATION_MODE=true` (the default in compose): a destination without real credentials is served by the mock platform
server (`mock-platforms`, port 9100), which behaves like Discourse, Dev.to and a signed partner webhook receiver and can be
told to fail on purpose (429 with reset time, timeout before or after creating, 500, renamed fields). Real Lemmy and Ghost
credentials come from the seed file (`CONNECTIONS_FILE`) and take priority. No real accounts are needed to run anything.

## Settings (all from environment variables, see `.env.example`)

| Variable | Default | Meaning |
|---|---|---|
| APP_ENV, LOG_LEVEL | dev, INFO | environment name, JSON log level |
| SIMULATION_MODE | true | use mock platforms where no real credentials exist |
| DATABASE_URL | sqlite (tests), Postgres (compose) | state, catalogue, certification runs |
| S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY | RustFS in compose | evidence storage |
| S3_ENABLED, S3_PUBLIC_URL | false (true in compose), - | also store evidence in S3; address used in download links |
| EVIDENCE_DIR | evidence/certification | local copy of the evidence |
| CONNECTIONS_FILE | - | JSON with credentials per destination (never stored in the database) |
| MOCK_PLATFORMS_URL | http://mock-platforms:9100 | where simulation mode finds the fake platforms |
| CALLBACK_HMAC_SECRET | change-me | signs job-finished callbacks (X-IncuBrix-Signature) |
| TEST_POST_TTL_SECONDS | 600 | test posts are deleted within this time |
| CERTIFY_MAX_CONSECUTIVE_FAILURES | 3 | a batch stops calling a connector after this many failures in a row |
| CERTIFY_RATE_LIMIT_WAIT_CAP_S | 30 | longest wait for a 429 during certification |
| HTTP_TIMEOUT_S, JOB_WORKERS, IDEMPOTENCY_PENDING_TIMEOUT_S | 10, 4, 120 | timeouts and workers |
| CALLBACK_MAX_ATTEMPTS, CALLBACK_BACKOFF_S | 3, 0.5 | callback delivery retries |
| IMPORT_WORKERS, IMPORT_FETCH_TIMEOUT_S | 32, 5 | concurrent site checks in a CSV import, and the timeout for each |
| IMPORT_ALLOW_PRIVATE_HOSTS | false (true in compose) | false = the importer refuses private and local addresses (SSRF protection) |
| IMPORT_DIR | evidence/imports | where import files are kept |
| RULES_EXTRACTOR, ANTHROPIC_API_KEY, RULES_MODEL | mock, -, claude-haiku-5-5 | who reads rules pages: the offline mock, or Claude |
| RULES_PRICE_PER_MTOK_IN / _OUT | 0 | prices used to estimate the cost in the output manifest |
| SERVICE_BASE_URL, FEED_TOKEN, FEED_MAX_ITEMS | http://localhost:8000, local-dev-feed-token, 50 | this service's own address (used in feed links), the token that lets a client write to feeds, items kept per feed |
| RECERT_INTERVAL_S | 0 (off) | seconds between automatic recertification runs |
| RECERT_MAX_AGE_S, RECERT_DEMOTED_COOLDOWN_S, RECERT_ATTENTION_RETRY_S, RECERT_BATCH_LIMIT | 7 days, 1 day, 1 hour, 25 | when a destination is due, and how many are checked per run |
| API_TOKEN | (off) | when set, every call except /health, /ready, the docs and public feeds needs `Authorization: Bearer <token>` or `X-API-Token` |
| RATE_LIMIT_PER_MINUTE, TRUST_PROXY | 0 (off), false | requests per minute per client (429 `RATE_LIMITED` with Retry-After); TRUST_PROXY=true uses X-Forwarded-For behind your own proxy |
| ALERT_WEBHOOK_URL | (off) | demotions and recoveries are also POSTed here, signed with the callback secret |
| MOCK_CONTROL_TOKEN (mock server) | (off) | the failure switches of the mock server then need the header `X-Mock-Token` |
| DISABLED_CONNECTORS | - | kill switch: those connectors answer CONNECTOR_DISABLED |

## Error codes

Every error is `{"error": {"code", "message", "retryable", "details"}}`.

| Code | HTTP | Retryable | Meaning |
|---|---|---|---|
| DEST_SPEC_VIOLATION | 422 | no | the post breaks the destination's limits or rules |
| DEST_RULE_FREQUENCY_CAP | 429 | yes (later) | frequency cap reached; `details.reschedule_at` says when |
| CONNECTION_AUTH_EXPIRED | 401 | no | credentials missing, wrong, expired or revoked |
| PLATFORM_RATE_LIMIT | 429 | yes | platform asked us to slow down; `details.retry_after_s` when known |
| PLATFORM_QUOTA_EXHAUSTED | 429 | yes (later) | daily or monthly quota used up |
| PLATFORM_CONTENT_REJECTED | 422 | no | the platform refused the content |
| PLATFORM_UNAVAILABLE | 503 | yes | platform down or timed out; `details.outcome_unknown` means it may have been created |
| CONNECTOR_DISABLED | 503 | no | kill switch is on for this connector |
| VALIDATION_ERROR | 422 | no | the request itself is malformed; `details.fields` lists them |
| NOT_FOUND | 404 | no | unknown destination, job or evidence file |
| METHOD_NOT_ALLOWED | 405 | no | wrong HTTP method |
| IDEMPOTENCY_KEY_REUSED | 409 | no | the key was used for a different request |
| IDEMPOTENCY_IN_PROGRESS | 409 | yes | the same request is still being processed |
| UNKNOWN_CONNECTOR | 404 | no | no such connector |
| UNKNOWN_METHOD | 404 | no | no such method, or the connector does not support it |
| UNAUTHORIZED | 401 | no | the access token is missing or wrong |
| RATE_LIMITED | 429 | yes | too many requests from this client; `Retry-After` and `details.retry_after_s` say how long to wait |
| INTERNAL_ERROR | 500 | no | unexpected error (details are in the logs, never in the response) |

## Where the proof is

`docs/EVIDENCE_LOG.md` lists every claim and its proof. Certification evidence per destination: `evidence/certification/`.
Manual demonstrations: `evidence/manual-demos/`. AI use: `AI_USE.md`. Sources and licences: `SOURCES.md`.
