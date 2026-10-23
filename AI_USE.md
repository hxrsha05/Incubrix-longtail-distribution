# AI_USE.md

AI tools used in this project, and what each was used for.

## Tools

| Tool | Model | Used for |
|---|---|---|
| Claude Code (Anthropic CLI / VS Code extension) | Claude Sonnet 5.5 | Reading and summarising the assessment briefs; discussing the architecture; writing most of the code, tests, Docker configuration and documentation listed below; running the tests and Docker commands; checking software versions and licences. |

No other AI coding tools were used.

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

## Manual work

No outputs have been edited by hand and presented as automated.
