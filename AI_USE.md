# AI_USE.md

AI tools used in this project, and what each was used for.

## Tools

| Tool | Model | Used for |
|---|---|---|
| Claude Code (Anthropic CLI / VS Code extension) | Claude Sonnet 5.5 | Reading and summarising the assessment briefs; discussing the architecture; writing most of the code, tests, Docker configuration and documentation listed below; running the tests and Docker commands; checking software versions and licences. |

No other AI coding tools were used.

**AI inside the product:** the rules-capture feature can call an AI model (Claude, through the Anthropic Messages API) to read a community's rules page. It is off by default (an offline keyword reader is used instead) and is switched on only with `RULES_EXTRACTOR=anthropic` and an API key. Whatever it proposes is checked against the page text and applied only after a named person confirms it. Results made with it are labelled as AI-generated in the output manifest. It has not been run against the live API during development.

## What the AI wrote

| Component | Files |
|---|---|
| Settings from environment variables | `app/config.py` |
| Shared error codes and JSON error format | `app/errors.py` |
| JSON logging and secret redaction | `app/logging_setup.py` |
| API entrypoint, health endpoints, correlation id | `app/main.py` |
| Connector contract models | `app/models.py` |
| Manifest loader | `app/connectors/manifest.py` |
| Connector base class and HTTP error mapping | `app/connectors/base.py` |
| Connector registry and kill switch | `app/connectors/registry.py` |
| Connector HTTP endpoint | `app/api/connector_routes.py` |
| Database tables | `app/db.py` |
| Idempotency store | `app/idempotency.py` |
| Posting-rules engine | `app/rules.py` |
| Publish pipeline (idempotency, rules, connector) | `app/publish_service.py` |
| Background jobs and signed callbacks | `app/jobs.py`, `app/api/job_routes.py` |
| Lemmy connector | `app/connectors/lemmy.py`, `app/connectors/manifests/lemmy.yaml` |
| Ghost connector | `app/connectors/ghost.py`, `app/connectors/manifests/ghost.yaml` |
| Signed webhook connector | `app/connectors/webhook.py`, `app/connectors/manifests/webhook.yaml` |
| Discourse connector | `app/connectors/discourse.py`, `app/connectors/manifests/discourse.yaml` |
| Dev.to connector | `app/connectors/devto.py`, `app/connectors/manifests/devto.yaml` |
| Assisted connector (assist packs) | `app/connectors/assisted.py`, `app/connectors/manifests/assisted.yaml` |
| Mock platform server (fake Discourse, Dev.to, partner webhook; failure switches) | `mock_platforms/main.py` |
| Destination catalogue and its API | `app/catalogue.py`, `app/api/catalogue_routes.py` |
| Certification harness, evidence storage (local and S3), credentials lookup, output manifest | `app/certification.py`, `app/certify_service.py`, `app/evidence.py`, `app/credentials.py`, `app/output_manifest.py` |
| Batch runner (command line) | `app/cli.py` |
| Three more connectors: MRSS feed (with the feed host), Hashnode, Kit | `app/connectors/feed.py`, `app/feeds.py`, `app/api/feed_routes.py`, `app/connectors/hashnode.py`, `app/connectors/kit.py`, their manifests |
| Recommender, its labelled test set and evaluation | `app/recommender.py`, `scripts/make_labelled_set.py`, `scripts/eval_recommender.py`, `samples/recommender/labelled_set.json` |
| Access token and rate limit, Idempotency-Key middleware | `app/http_guard.py` |
| Publish by destination, alerts, destination record and levels, audience relevance | `app/api/publish_routes.py`, `app/alerts.py`, `app/api/alert_routes.py`, `app/records.py`, `app/audience.py` |
| Hard-case end-to-end suite | `tests/test_hard_cases.py` |
| Recertification scheduler with auto-demotion | `app/recertify.py`, `app/api/recert_routes.py`, `scripts/demo_recertification.py` |
| AI rules capture (proposal, verification, human confirmation) and its API | `app/rules_capture.py`, `app/api/rules_routes.py` |
| Tailoring and relevance score, with their API | `app/tailoring.py`, `app/relevance.py`, `app/api/advice_routes.py` |
| CSV import (validate, de-duplicate, reachability, enrichment, report) and its job and routes | `app/csv_import.py`, `app/import_service.py`, `app/api/import_routes.py`, `scripts/make_sample_csv.py`, `scripts/import_demo.py`, `samples/catalogue/` |
| Docker setup and seed scripts | `Dockerfile`, `docker-compose.yml`, `scripts/seed_local.py`, `scripts/seed_catalogue.py` |
| OpenAPI export and secret scan | `scripts/export_openapi.py`, `scripts/secret_scan.py`, `openapi.yaml` |
| Tests | `tests/` |
| Documentation | `README.md`, `AI_USE.md`, `SOURCES.md`, `docs/EVIDENCE_LOG.md` |

## Decisions made by me

- Chose assessment 4 (Long-Tail Distribution) after reading all four briefs.
- Accepted the recommendation to use Python and FastAPI.
- Decided to build and test everything against local Lemmy and Ghost instances, with no posts to real communities.
- Created the private repository and gave the reviewer access.
- Decided which failures demote a destination (a changed platform response, or a rejected valid post) and which only mark it "needs attention" (rate limit, expired token, outage).
- Chose to certify the real local Lemmy and Ghost, and the mock server for platforms that cannot run on a laptop.
- Took on the signed webhook connector as a learning exercise; in the end the AI wrote the connector so that time was spent on the remaining work. I review and explain the code before the live session.

## Corrections made along the way

- The AI first specified MinIO for object storage. Its container image is no longer published, so the first image download failed. It was replaced with RustFS, an S3-compatible store.
- The AI first assumed the Ghost 6 admin key was `id` plus `secret`; the real server returns the full key in the secret field. Found by testing against the live instance.
- The AI first assumed a draft post on Ghost is private; the real server gives it an unlisted public preview link. The test and the documentation were corrected.

- The first secret scan flagged Lemmy's own database printing session tokens in its logs. That is a third-party test container, so the scan now covers only the logs of the services we wrote, and the report says so.
- The first version of the credentials lookup fell back to the mock accounts even when a destination named a connection that did not exist. A test caught this; a named connection must now exist.
- Some of the AI's own tests had wrong expectations (a miscounted batch, a message wording) and were corrected after the run; the code under test was right.
- A destination with no posting rules is refused by design (the rules engine fails closed). The first batch-runner test hit this; the seed destinations now state all five rules.

- The first 1,000-row test file had 20 "duplicates" written in upper case including the path; paths are case-sensitive, so they were correctly treated as different, missing pages. The file generator was changed to vary only the scheme and host, and the counts now match what the generator put in.
- The importer accepts single-word host names (docker service names) only in local-demo mode; in normal mode they are refused as invalid.

- The test suite caught two real faults in the AI's new code: a replayed import upload rewrote the input file while the first job was reading it (the job saw an empty CSV), and two "could not run" certification records created in the same millisecond got the same id. Files are now written atomically, an upload never rewrites an existing input, and record ids are random.

- The AI's first word-boundary truncation kept a rough 40% of the text before backing up to a space, which cut "quick" to "quic" at one limit. A test caught it; the rule is now: always cut at a word, except inside one single enormous word.

- Email sends real messages to real people, so the AI chose to make the Kit connector draft-only (a scheduled send is possible only when a destination asks for it; sending immediately is refused), and certification treats "draft" as the expected state for it.

- The AI wrote both the recommender's synonym table and the labelled test set, so they share a vocabulary. To show how much that matters it measured the recommender with the synonym table switched off (94.3% against 96.7%), and the evaluation report carries a caution that the figures are optimistic and the set is synthetic.
- The "150 real destinations" were first left undone because the AI would not invent entries. They were then researched for real: five AI research agents (Claude) browsed public pages and wrote JSON files into `samples/research/`, each rules quote copied word for word from the community's own page and checked as an exact substring. The AI-written `scripts/build_real_catalogue.py` re-checked every address on the real internet (reachability, robots.txt, platform, language) and applied the quoted rules. Limits: the agents could not read pages that block bots (listed with a warning), rules are unconfirmed by a person, and one agent deleted a working file belonging to another agent (no effect on the results). All 152 entries are catalogue only; nothing was posted anywhere.
- A demotion initially only changed a label. The AI made it bite: a demoted destination is refused at publish time and left out of recommendations.

- Running the new build in Docker crashed on startup: the image had installed a much newer FastAPI (0.161, Starlette 1.9) than the one the tests ran on (0.115, Starlette 0.46), and the newer one no longer has `add_event_handler`. The AI replaced it with the lifespan mechanism (works on both), added a test for it, and pinned the dependencies in `requirements.txt` to the versions the tests ran on, so the container and the test machine now match.

- Re-reading the brief in full (the AI had worked from a summary) showed several misses, which the AI then fixed: the Advanced relevance score is a score of each DESTINATION against IncuBrix's audience (the AI had built a content-to-destination fit instead, which is now a second, separate feature); the batch runner must call the API (the AI had run the application in-process); an Idempotency-Key header is required on every POST (only some routes had it); anything slower than 2 seconds must be a job (rules capture was synchronous); the manifest must name models and a seed; the destination record and levels L0 to L4 had no implementation; destination format limits were checked by a method but not enforced before publishing; Lemmy and Discourse claimed to take images but ignored media; a public demo needs a token and a rate limit.

## Manual work

No outputs have been edited by hand and presented as automated.
