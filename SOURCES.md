# SOURCES.md — every library, image, model and data source

> Required by the IncuBrix brief: every model, API, library, dataset and media source, each with a **link to its
> commercial-use terms**. Everything in the default path and everything it outputs must allow commercial use.
> The brief says: *"Avoid AGPL code in the shipped service."*

Status: **TO VERIFY** = recalled from memory, I must open the licence link and confirm before submitting.

## Shipped service (runs inside our `api` container)

| Name | Version | What for | Licence | Link | Status |
|---|---|---|---|---|---|
| Python | 3.12 | Runtime | PSF | https://docs.python.org/3/license.html | TO VERIFY |
| FastAPI | ≥0.115 | HTTP API | MIT | https://github.com/fastapi/fastapi | TO VERIFY |
| Uvicorn | ≥0.30 | Server | BSD-3-Clause | https://github.com/encode/uvicorn | TO VERIFY |
| Pydantic / pydantic-settings | ≥2.7 / ≥2.3 | Data models, settings | MIT | https://github.com/pydantic/pydantic | TO VERIFY |
| PyYAML | ≥6 | Manifest files | MIT | https://github.com/yaml/pyyaml | TO VERIFY |
| httpx | ≥0.27 | HTTP client | BSD-3-Clause | https://github.com/encode/httpx | TO VERIFY |
| SQLAlchemy | ≥2.0 | Database access | MIT | https://github.com/sqlalchemy/sqlalchemy | TO VERIFY |
| psycopg (v3) | ≥3.1 | Postgres driver | LGPL-3.0 (used as an unmodified library, not AGPL) | https://github.com/psycopg/psycopg | TO VERIFY |
| boto3 / botocore | ≥1.34 | S3 client for certification evidence | Apache-2.0 | https://github.com/boto/boto3 | TO VERIFY |
| pytest | ≥8 | Tests (dev only) | MIT | https://github.com/pytest-dev/pytest | TO VERIFY |

> No extra library was added for the Hashnode, Kit and feed connectors: they use httpx and the Python standard library (xml.etree).
> Hashnode API terms: https://hashnode.com/terms  ·  Kit API documentation and terms: https://developers.kit.com/ and https://kit.com/terms  (TO VERIFY; the connectors were not run against real accounts).

## Infrastructure containers (docker compose)

| Name | Version | What for | Licence | Link | Status |
|---|---|---|---|---|---|
| PostgreSQL | 16-alpine | Our database | PostgreSQL Licence | https://www.postgresql.org/about/licence/ | TO VERIFY |
| RustFS | 1.0.3 | S3-compatible storage | Apache-2.0 | https://github.com/rustfs/rustfs | TO VERIFY |
| Alpine Linux | 3.20 | One-off permission fix for the storage volume | MIT (plus package licences) | https://alpinelinux.org/ | TO VERIFY |

> MinIO was the brief's suggestion, but it no longer publishes container images, so RustFS (any S3-compatible store is allowed) is used instead.

## Test targets (separate containers we post test content to — NOT part of the shipped service)

These run only as throwaway local instances so certification can publish real test posts without touching any real community.

| Name | Version | What for | Licence | Link | Status |
|---|---|---|---|---|---|
| Lemmy (server) | 0.19.20 | Local Lemmy test destination | **AGPL-3.0** | https://github.com/LemmyNet/lemmy | TO VERIFY |
| Lemmy UI | 0.19.20 | Browser view of the test posts (for screenshots) | **AGPL-3.0** | https://github.com/LemmyNet/lemmy-ui | TO VERIFY |
| pict-rs | 0.5 | Lemmy's image service | **AGPL-3.0** | https://git.asonix.dog/asonix/pict-rs | TO VERIFY |
| Ghost | 6.72.0 | Local Ghost test destination | MIT | https://github.com/TryGhost/Ghost | TO VERIFY |
| MySQL | 8.0 | Ghost's database | GPL-2.0 (Community) | https://www.mysql.com/about/legal/licensing/oem/ | TO VERIFY |

> **AGPL note:** Lemmy, Lemmy UI and pict-rs are AGPL. We do **not** copy, link or ship their code. They run as separate,
> unmodified containers that our service talks to over HTTP, purely as test targets. Our service has no AGPL code.
> To confirm with IncuBrix by email if there is any doubt.

## Models, APIs, datasets, media

| Name | What for | Licence / terms | Link | Status |
|---|---|---|---|---|
| Anthropic Claude (Messages API), model `claude-haiku-5-5` | Optional: reads a community's rules page in the rules-capture feature (off by default; the mock reader is used) | Anthropic commercial terms | https://www.anthropic.com/legal/commercial-terms | TO VERIFY (not used during development) |

Add here when used: any LLM for rules capture / relevance scoring (provider + model + terms link + cost), any dataset,
any image/media used in test posts (only own, public-domain or properly licensed media).

## TODO before submission

- [ ] Open every link above and confirm the licence; change status to VERIFIED with the date.
- [ ] Add every new library/image/model the moment it is added to `requirements.txt` or `docker-compose.yml`.
- [ ] Add the LLM provider (if used) with its commercial-use terms link and cost per call.
- [ ] Add the real catalogue's data sources (if the 150-destination catalogue is attempted): where each fact came from.
- [ ] Confirm no research-only / non-commercial item (for example CC BY-NC) is in the default path.
