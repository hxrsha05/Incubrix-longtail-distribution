# SOURCES.md: every library, image, model, API and data source

The brief requires every model, API, library, dataset and media source with a link to its commercial-use terms, and says to avoid AGPL code in the
shipped service. **Verified** means I opened the source named in the "How checked" column on 23 Oct 2026 and read the licence; **Not verified** says what is
missing. Versions are the ones pinned in `requirements.txt` (the versions the tests ran on).

## Shipped service (runs inside our `api` container)

| Name | Version | What for | Licence | Link | How checked |
|---|---|---|---|---|---|
| Python | 3.12 (container), 3.10 (tests) | Runtime | PSF License v2 (permits commercial use) | https://docs.python.org/3/license.html | Verified: page read |
| FastAPI | 0.115.13 | HTTP API | MIT | https://github.com/fastapi/fastapi | Verified: installed package metadata (classifier "MIT License") |
| Starlette | 0.46.2 | Web toolkit under FastAPI | BSD-3-Clause | https://github.com/encode/starlette | Verified: installed package metadata |
| Uvicorn | 0.34.3 | Server | BSD-3-Clause | https://github.com/encode/uvicorn | Verified: installed package metadata |
| Pydantic | 2.13.4 | Data models | MIT | https://github.com/pydantic/pydantic | Verified: installed package metadata |
| pydantic-settings | 2.10.0 | Settings from the environment | MIT | https://github.com/pydantic/pydantic-settings | Verified: installed package metadata |
| PyYAML | 6.0.2 | Manifest files | MIT | https://github.com/yaml/pyyaml | Verified: installed package metadata |
| httpx | 0.28.1 | HTTP client | BSD-3-Clause | https://github.com/encode/httpx | Verified: installed package metadata |
| SQLAlchemy | 2.0.41 | Database access | MIT | https://github.com/sqlalchemy/sqlalchemy | Verified: installed package metadata |
| psycopg (v3, binary) | >=3.1,<4 | Postgres driver (docker compose) | **LGPL-3.0**, used as an unmodified library and not linked into our code, not AGPL | https://github.com/psycopg/psycopg | Verified: repository page |
| boto3 / botocore | 1.43.117 | S3 client for evidence files | Apache-2.0 | https://github.com/boto/boto3 | Verified: installed package metadata |
| anyio, h11, httpcore | (dependencies of the above) | async plumbing | MIT, MIT, BSD-3-Clause | https://github.com/agronholm/anyio | Verified: installed package metadata |
| pytest | 9.1.1 (dev only, not shipped) | Tests | MIT | https://github.com/pytest-dev/pytest | Verified: installed package metadata |
| pypdf, openpyxl | (my tooling only, not shipped) | reading the brief, filling the form | BSD-3-Clause, MIT | https://github.com/py-pdf/pypdf | Verified: installed package metadata |

## Infrastructure containers (docker compose)

| Name | Version | What for | Licence | Link | How checked |
|---|---|---|---|---|---|
| PostgreSQL | 16-alpine | Our database | PostgreSQL Licence ("for any purpose, without fee") | https://www.postgresql.org/about/licence/ | Verified: page read |
| RustFS | 1.0.3 | S3-compatible storage | Apache-2.0 | https://github.com/rustfs/rustfs | Verified: repository page |
| Alpine Linux | 3.20 | One-off permission fix for the storage volume | Free software; the licence of each package is listed in Alpine's package index (the Docker page says so) | https://hub.docker.com/_/alpine | Partly verified: the image page confirms per-package licences; I did not audit each package. It runs once to set a folder's owner and ships nothing |

> MinIO was the brief's suggestion, but it no longer publishes container images, so RustFS (any S3-compatible store is allowed) is used instead.

## Test targets (separate containers we post test content to; NOT part of the shipped service)

These run only as throwaway local instances so certification can publish real test posts without touching any real community.

| Name | Version | What for | Licence | Link | How checked |
|---|---|---|---|---|---|
| Lemmy (server) | 0.19.20 | Local Lemmy test destination | **AGPL-3.0** | https://github.com/LemmyNet/lemmy | Verified: repository page |
| Lemmy UI | 0.19.20 | Browser view of the test posts (for screenshots) | **AGPL-3.0** | https://github.com/LemmyNet/lemmy-ui | Verified: repository page |
| pict-rs | 0.5 | Lemmy's image service | **AGPL-3.0** (Alpine's package index says AGPL-3.0-or-later; the project README text is older and says GPL) | https://git.asonix.dog/asonix/pict-rs | Partly verified: the project site blocked my fetch (an anti-bot page); the AGPL licence comes from package listings found by search |
| Ghost | 6.72.0 | Local Ghost test destination | MIT | https://github.com/TryGhost/Ghost | Verified: repository page |
| MySQL Community Server | 8.0 | Ghost's database | **GPL-2.0** (with additional linking permissions) | https://github.com/mysql/mysql-server | Verified: the repository's LICENSE file |

> **AGPL note:** Lemmy, Lemmy UI and pict-rs are AGPL. We do **not** copy, link or ship their code. They run as separate, unmodified containers that our
> service talks to over HTTP, purely as test targets, and they are not in the demo image (`Dockerfile.demo`). Our shipped service contains no AGPL code.
> MySQL (GPL-2.0) is likewise only Ghost's own database in a separate container. If IncuBrix sees any issue with test targets under these licences, they
> can be replaced by the mock server for that platform.

## APIs we talk to (and whether the connector was proven against the real thing)

| Platform | What for | Terms / documentation | Status of the connector |
|---|---|---|---|
| Lemmy API v3 | publish, read, delete | https://join-lemmy.org/api/ | **Proven** against a real local Lemmy 0.19.20 (`tests/live/`, certification evidence) |
| Ghost Admin API | publish, read, delete | https://ghost.org/docs/admin-api/ | **Proven** against a real local Ghost 6.72.0 |
| Discourse API | publish, read, delete | https://docs.discourse.org/ | **Mock only.** Modelled on the documented behaviour; never run against a real Discourse |
| Dev.to (Forem) API | publish, read, unpublish | https://developers.forem.com/api | **Mock only.** Dev.to has no article delete, so "delete" is unpublish |
| Hashnode GraphQL API | publish, read, delete | https://hashnode.com/terms (terms); API at https://gql.hashnode.com | **Mock only, partly confirmed.** Hashnode's own post (https://hashnode.com/blog/hashnode-gql-agent-skill) confirms the `publishPost` mutation and token authentication and says **write operations need the publication to be on Hashnode Pro**. I could not confirm the `removePost` mutation name or the input field names from official documentation |
| Kit API v4 | broadcasts as drafts | https://developers.kit.com/api-reference/broadcasts/create-a-broadcast | **Mock only, documentation checked.** I read Kit's reference for create (`POST /v4/broadcasts`, header `X-Kit-Api-Key`, `send_at: null` saves a draft, required fields) and delete (`DELETE /v4/broadcasts/{id}`, 204, drafts and scheduled only); the connector and the mock follow it. Never run against a real Kit account; the connector never sends mail by itself |
| MRSS feed | publish into a feed we host | https://www.rssboard.org/media-rss | **Proven end to end** (we host the feed and read it back) |
| Anthropic Messages API (model `claude-haiku-5-5`) | optional: reads a rules page in the rules-capture feature | https://www.anthropic.com/legal/commercial-terms (Verified: the page says the customer owns the outputs, subject to the terms and usage policy) | **Off by default** (an offline keyword reader is used). Tested against a fake API only; not run against the live API |

## Models, datasets, media

| Name | What for | Licence / terms | How checked |
|---|---|---|---|
| none in the default path | The default path uses no AI model, no dataset and no media | n/a | n/a |
| Anthropic Claude (optional, see above) | rules capture, only when switched on with a key | Anthropic commercial terms | Verified: terms page |
| Synthetic labelled set (`samples/recommender/labelled_set.json`) | measuring the recommender | generated by `scripts/make_labelled_set.py` (own work) | n/a |
| Test posts and images | certification posts are plain text written by the service; the media in tests are example addresses, not files | n/a | n/a |

## Not verified, so please read

- pict-rs and Alpine: see the notes above.
- Discourse, Dev.to, Hashnode and Kit behaviour is from documentation and mocks, not from real accounts.
- This file lists what is in this repository on the day of submission; run `pip list` in the container to see the full dependency tree.
