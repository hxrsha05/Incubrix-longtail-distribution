# AI_USE.md — AI tools used in this project

> Required by the IncuBrix brief: *"every AI coding tool you used (and for what)"*.
> Keep this honest and current. Update it at the end of every working session.

## Tools

| Tool | Model | Used for |
|---|---|---|
| Claude Code (Anthropic CLI / VS Code extension) | Claude Sonnet 5.5 | Reading and summarising the assessment briefs; designing the architecture together with me; writing most of the code, tests, Docker files and documentation listed below; running tests and Docker commands; researching versions and licences. |

_No other AI coding tools used so far. (Add any others here, for example ChatGPT, Copilot, Cursor.)_

## What the AI wrote, and what I reviewed

Status column = my own honest status. **Do not tick "understood" unless I really can explain it without help.**

| Component | Files | AI-written | I reviewed | I can explain it |
|---|---|---|---|---|
| Settings from environment | `app/config.py` | Yes | [ ] | [ ] |
| Shared error codes + JSON error format | `app/errors.py` | Yes | [ ] | [ ] |
| JSON logging + secret redaction | `app/logging_setup.py` | Yes | [ ] | [ ] |
| API entrypoint, health, correlation id | `app/main.py` | Yes | [ ] | [ ] |
| Connector contract models | `app/models.py` | Yes | [ ] | [ ] |
| Manifest loader | `app/connectors/manifest.py` | Yes | [ ] | [ ] |
| Connector base class + HTTP error mapping | `app/connectors/base.py` | Yes | [ ] | [ ] |
| Connector registry + kill switch | `app/connectors/registry.py` | Yes | [ ] | [ ] |
| Connector HTTP door | `app/api/connector_routes.py` | Yes | [ ] | [ ] |
| Database tables | `app/db.py` | Yes | [ ] | [ ] |
| Idempotency store | `app/idempotency.py` | Yes | [ ] | [ ] |
| Rules engine | `app/rules.py` | Yes | [ ] | [ ] |
| Publish pipeline (idempotency → rules → connector) | `app/publish_service.py` | Yes | [ ] | [ ] |
| Background jobs + signed callbacks | `app/jobs.py`, `app/api/job_routes.py` | Yes | [ ] | [ ] |
| Lemmy connector | `app/connectors/lemmy.py`, `manifests/lemmy.yaml` | Yes | [ ] | [ ] |
| Docker setup | `Dockerfile`, `docker-compose.yml`, `scripts/seed_local.py` | Yes | [ ] | [ ] |
| Tests | `tests/*` | Yes | [ ] | [ ] |

## Decisions that were mine (not the AI's)

_Fill in as they happen, in my own words. Examples: chose PS4 over the other three; chose Python/FastAPI; chose which extra connectors to build._

- Chose problem statement 4 (Long-Tail Distribution).
-

## Things the AI got wrong or I had to correct

_Honesty scores well. Record real examples, however small._

- MinIO's container image was no longer published, so the first image download failed; replaced with RustFS (found by checking, not assumed).
-

## Manual work disclosure

The brief disqualifies *"hidden manual work presented as automated"*. List anything done by hand here (for example hand-written catalogue rows, hand-labelled recommender data, screenshots taken manually):

- _(none so far)_

## TODO before submission

- [ ] Read every file in the table above and tick the two right-hand columns honestly.
- [ ] Add any other AI tool I use (even for small things).
- [ ] List anything done by hand under "Manual work disclosure".
- [ ] Copy the tool names into the "AI coding tools used" field of the Excel form (tab `1_Your_Details`).
