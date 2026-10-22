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
| Mock platform server (fake Discourse, Dev.to, partner webhook; failure switches) | `mock_platforms/main.py` |
| Docker setup and seed script | `Dockerfile`, `docker-compose.yml`, `scripts/seed_local.py` |
| Tests | `tests/` |
| Documentation | `AI_USE.md`, `SOURCES.md`, `docs/EVIDENCE_LOG.md` |

## Decisions made by me

- Chose assessment 4 (Long-Tail Distribution) after reading all four briefs.
- Accepted the recommendation to use Python and FastAPI.
- Decided to build and test everything against local Lemmy and Ghost instances, with no posts to real communities.
- Created the private repository and gave the reviewer access.

## Corrections made along the way

- The AI first specified MinIO for object storage. Its container image is no longer published, so the first image download failed. It was replaced with RustFS, an S3-compatible store.
- The AI first assumed the Ghost 6 admin key was `id` plus `secret`; the real server returns the full key in the secret field. Found by testing against the live instance.
- The AI first assumed a draft post on Ghost is private; the real server gives it an unlisted public preview link. The test and the documentation were corrected.

## Manual work

No outputs have been edited by hand and presented as automated.
