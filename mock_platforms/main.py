"""A small fake website that pretends to be Discourse, Dev.to and a partner site's webhook receiver.

Why it exists: some platforms cannot run on a laptop (Discourse is heavy, Dev.to is a public company's site), and a
real platform will never time out or rate-limit us on request. This one can. It is used for:
  * simulation mode (the whole system runs with no real accounts),
  * testing the Discourse / Dev.to / webhook connectors,
  * the hard cases: slow answers, "slow down" replies, rejected passwords, a renamed field.

HONESTY NOTE: the Discourse and Dev.to behaviour here is MODELLED on how those platforms are documented to behave.
It has NOT been verified against a real Discourse or a real Dev.to.

Everything is kept in memory. POST /__control/reset wipes it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

PLATFORMS = {"discourse", "devto", "partner", "hashnode", "kit"}
_OPS = {"POST": "create", "GET": "read", "DELETE": "delete", "PUT": "update"}


@dataclass
class FailureRule:
    platform: str  # discourse | devto | partner
    op: str = "any"  # create | read | update | delete | any
    kind: str = "status"  # status | timeout_before | timeout_after | rename_field
    status: int = 500
    retry_after: float | None = None
    delay_s: float = 3.0
    remaining: int | None = 1  # how many times it fires; None = until reset
    rename: dict[str, str] = field(default_factory=dict)

    def matches(self, platform: str, op: str) -> bool:
        return self.platform in (platform, "*") and self.op in (op, "any") and (self.remaining is None or self.remaining > 0)


class MockState:
    def __init__(self) -> None:
        self.discourse_key = os.environ.get("MOCK_DISCOURSE_KEY", "mock-discourse-key")
        self.devto_key = os.environ.get("MOCK_DEVTO_KEY", "mock-devto-key")
        self.hashnode_key = os.environ.get("MOCK_HASHNODE_KEY", "mock-hashnode-token")
        self.kit_key = os.environ.get("MOCK_KIT_KEY", "mock-kit-key")
        self.hashnode_posts: dict[str, dict[str, Any]] = {}  # hashnode
        self.kit_broadcasts: dict[int, dict[str, Any]] = {}  # kit
        self.partner_secrets = {"demo-partner": os.environ.get("MOCK_PARTNER_SECRET", "mock-partner-secret")}
        self.next_id = 100
        self.topics: dict[int, dict[str, Any]] = {}  # discourse
        self.articles: dict[int, dict[str, Any]] = {}  # devto
        self.partner_posts: dict[str, dict[str, dict[str, Any]]] = {}  # partner -> id -> post
        self.rules: list[FailureRule] = []

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def take_rule(self, platform: str, op: str, kinds: set[str]) -> FailureRule | None:
        for rule in self.rules:
            if rule.kind in kinds and rule.matches(platform, op):
                if rule.remaining is not None:
                    rule.remaining -= 1
                return rule
        return None


def _platform_of(path: str) -> str | None:
    first = path.strip("/").split("/", 1)[0]
    return first if first in PLATFORMS else None


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "topic"


def _rename_keys(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {mapping.get(k, k): _rename_keys(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename_keys(v, mapping) for v in value]
    return value


def _failure_body(platform: str, status: int, retry_after: float | None) -> dict[str, Any]:
    wait = int(retry_after or 0)
    if platform == "discourse":
        if status == 429:
            return {"errors": [f"You've performed this action too many times. Please wait {wait} seconds before trying again."],
                    "error_type": "rate_limit", "extras": {"wait_seconds": wait, "time_left": f"{wait} seconds"}}
        if status in (401, 403):
            return {"errors": ["You are not permitted to view the requested resource. The API username or key is invalid."], "error_type": "invalid_access"}
        return {"errors": ["Something went wrong"]}
    if platform == "devto":
        messages = {429: f"Rate limit reached, try again in {wait} seconds", 401: "unauthorized"}
        return {"error": messages.get(status, "internal error"), "status": status}
    messages = {429: "rate_limited", 410: "permission_revoked", 401: "invalid_signature"}
    return {"error": messages.get(status, "internal_error")}


def create_app() -> FastAPI:
    app = FastAPI(title="IncuBrix mock platforms")
    state = MockState()
    app.state.mock = state

    # ------------------------------------------------------------------ failure switches (middleware)
    @app.middleware("http")
    async def failure_switches(request: Request, call_next):
        platform = _platform_of(request.url.path)
        if platform is None:
            return await call_next(request)
        op = _OPS.get(request.method, "any")

        rule = state.take_rule(platform, op, {"status", "timeout_before"})
        if rule and rule.kind == "timeout_before":
            await asyncio.sleep(rule.delay_s)  # the caller gives up; nothing is created
            return JSONResponse({"error": "gateway timeout"}, status_code=504)
        if rule and rule.kind == "status":
            headers = {"Retry-After": str(int(rule.retry_after))} if rule.retry_after is not None else {}
            return JSONResponse(_failure_body(platform, rule.status, rule.retry_after), status_code=rule.status, headers=headers)

        late = state.take_rule(platform, op, {"timeout_after"})
        response = await call_next(request)  # the post IS created here
        if late:
            await asyncio.sleep(late.delay_s)  # ...but the answer arrives too late for the caller

        renames = {}
        for r in state.rules:
            if r.kind == "rename_field" and r.platform in (platform, "*") and (r.remaining is None or r.remaining > 0):
                renames.update(r.rename)
        if renames and response.headers.get("content-type", "").startswith("application/json"):
            body = b"".join([chunk async for chunk in response.body_iterator])
            try:
                renamed = _rename_keys(json.loads(body), renames)
                return JSONResponse(renamed, status_code=response.status_code)
            except ValueError:
                return Response(body, status_code=response.status_code, media_type="application/json")
        return response

    # ------------------------------------------------------------------ control
    @app.middleware("http")
    async def control_token(request: Request, call_next):
        """On a public demo set MOCK_CONTROL_TOKEN: the failure switches then need the header X-Mock-Token (the fake platforms themselves stay open)."""
        token = os.environ.get("MOCK_CONTROL_TOKEN")
        if token and request.url.path.startswith("/__control") and request.headers.get("X-Mock-Token") != token:
            return JSONResponse({"error": "the failure switches need the header X-Mock-Token"}, status_code=401)
        return await call_next(request)

    @app.post("/__control/reset")
    async def reset() -> dict:
        fresh = MockState()
        state.__dict__.update(fresh.__dict__)
        return {"status": "reset"}

    @app.post("/__control/fail")
    async def add_rule(body: dict) -> dict:
        kind = body.get("kind", "status")
        # a renamed field stays renamed until reset; every other failure fires once unless told otherwise
        remaining = body["count"] if "count" in body else (None if kind == "rename_field" else 1)
        rule = FailureRule(
            platform=body["platform"], op=body.get("op", "any"), kind=kind,
            status=int(body.get("status", 500)), retry_after=body.get("retry_after"), delay_s=float(body.get("delay_s", 3)),
            remaining=remaining, rename=body.get("rename") or {},
        )
        state.rules.append(rule)
        return {"rules": len(state.rules)}

    @app.delete("/__control/fail")
    async def clear_rules() -> dict:
        state.rules.clear()
        return {"rules": 0}

    @app.post("/__control/metrics")
    async def set_metrics(body: dict) -> dict:
        store = {"discourse": state.topics, "devto": state.articles, "hashnode": state.hashnode_posts, "kit": state.kit_broadcasts}[body["platform"]]
        key = body["id"] if body["platform"] == "hashnode" else int(body["id"])
        store[key].update(body.get("values", {}))
        return {"ok": True}

    @app.get("/__control/partner_posts")
    async def partner_posts_dump() -> list:
        return [p for posts in state.partner_posts.values() for p in posts.values()]

    @app.get("/__control/state")
    async def dump() -> dict:
        return {
            "discourse_topics": len(state.topics), "devto_articles": len(state.articles),
            "hashnode_posts": len(state.hashnode_posts), "kit_broadcasts": len(state.kit_broadcasts),
            "partner_posts": sum(len(v) for v in state.partner_posts.values()),
            "rules": [r.__dict__ for r in state.rules],
        }

    # ------------------------------------------------------------------ Discourse (modelled)
    def discourse_auth(request: Request) -> JSONResponse | None:
        if request.headers.get("Api-Key") != state.discourse_key or not request.headers.get("Api-Username"):
            return JSONResponse(_failure_body("discourse", 403, None), status_code=403)
        return None

    @app.post("/discourse/posts.json")
    async def discourse_create(request: Request):
        if (denied := discourse_auth(request)) is not None:
            return denied
        body = await request.json()
        title, raw = str(body.get("title", "")), str(body.get("raw", ""))
        errors = []
        if len(title) < 15:
            errors.append("Title is too short (minimum is 15 characters)")
        if len(title) > 255:
            errors.append("Title is too long (maximum is 255 characters)")
        if len(raw) < 20:
            errors.append("Body is too short (minimum is 20 characters)")
        if len(raw) > 32000:
            errors.append("Body is too long (maximum is 32000 characters)")
        if any(t["title"] == title and not t["deleted"] for t in state.topics.values()):
            errors.append("Title has already been used")
        if errors:
            return JSONResponse({"action": "create_post", "errors": errors}, status_code=422)
        topic_id, post_id = state.new_id(), state.new_id()
        state.topics[topic_id] = {
            "id": topic_id, "title": title, "raw": raw, "slug": _slug(title), "post_id": post_id, "deleted": False,
            "username": request.headers["Api-Username"], "created_at": time.time(), "views": 0, "like_count": 0, "reply_count": 0,
        }
        return {"id": post_id, "topic_id": topic_id, "topic_slug": _slug(title), "raw": raw}

    @app.get("/discourse/t/{topic_id}.json")
    async def discourse_get(topic_id: int, request: Request):
        topic = state.topics.get(topic_id)
        if topic is None or topic["deleted"]:
            return JSONResponse({"errors": ["The requested URL or resource could not be found."], "error_type": "not_found"}, status_code=404)
        return {"id": topic["id"], "title": topic["title"], "slug": topic["slug"], "posts_count": 1 + topic["reply_count"],
                "views": topic["views"], "like_count": topic["like_count"], "reply_count": topic["reply_count"]}

    @app.delete("/discourse/t/{topic_id}.json")
    async def discourse_delete(topic_id: int, request: Request):
        if (denied := discourse_auth(request)) is not None:
            return denied
        topic = state.topics.get(topic_id)
        if topic is None or topic["deleted"]:
            return JSONResponse({"errors": ["The requested URL or resource could not be found."], "error_type": "not_found"}, status_code=404)
        topic["deleted"] = True
        return Response(status_code=200)

    @app.get("/discourse/topics/created-by/{username}.json")
    async def discourse_by_user(username: str, request: Request):
        if (denied := discourse_auth(request)) is not None:
            return denied
        topics = [{"id": t["id"], "title": t["title"], "slug": t["slug"]} for t in state.topics.values() if t["username"] == username and not t["deleted"]]
        return {"topic_list": {"topics": topics}}

    # ------------------------------------------------------------------ Dev.to (modelled)
    def devto_auth(request: Request) -> JSONResponse | None:
        if request.headers.get("api-key") != state.devto_key:
            return JSONResponse({"error": "unauthorized", "status": 401}, status_code=401)
        return None

    def devto_view(a: dict[str, Any]) -> dict[str, Any]:
        return {"id": a["id"], "title": a["title"], "slug": a["slug"], "published": a["published"],
                "url": f"http://devto.mock/mock_user/{a['slug']}" if a["published"] else None,
                "canonical_url": a.get("canonical_url"), "page_views_count": a["views"],
                "public_reactions_count": a["likes"], "comments_count": a["comments"]}

    @app.post("/devto/api/articles")
    async def devto_create(request: Request):
        if (denied := devto_auth(request)) is not None:
            return denied
        article = (await request.json()).get("article", {})
        title = str(article.get("title", "")).strip()
        if not title:
            return JSONResponse({"error": "Title can't be blank", "status": 422}, status_code=422)
        tags = [t.strip() for t in str(article.get("tags", "")).split(",") if t.strip()]
        if len(tags) > 4:
            return JSONResponse({"error": "Tag list exceed the maximum of 4 tags", "status": 422}, status_code=422)
        article_id = state.new_id()
        slug = f"{_slug(title)}-{article_id:x}"
        state.articles[article_id] = {
            "id": article_id, "title": title, "slug": slug, "body": article.get("body_markdown", ""),
            "published": bool(article.get("published", False)), "canonical_url": article.get("canonical_url"),
            "views": 0, "likes": 0, "comments": 0,
        }
        return JSONResponse(devto_view(state.articles[article_id]), status_code=201)

    @app.get("/devto/api/articles/me/all")
    async def devto_mine(request: Request):
        if (denied := devto_auth(request)) is not None:
            return denied
        return [devto_view(a) for a in reversed(list(state.articles.values()))]

    @app.get("/devto/api/articles/{article_id}")
    async def devto_get(article_id: int):
        a = state.articles.get(article_id)
        if a is None or not a["published"]:  # drafts are not public
            return JSONResponse({"error": "not found", "status": 404}, status_code=404)
        return devto_view(a)

    @app.put("/devto/api/articles/{article_id}")
    async def devto_update(article_id: int, request: Request):
        if (denied := devto_auth(request)) is not None:
            return denied
        a = state.articles.get(article_id)
        if a is None:
            return JSONResponse({"error": "not found", "status": 404}, status_code=404)
        changes = (await request.json()).get("article", {})
        if "published" in changes:
            a["published"] = bool(changes["published"])  # published:false reverts it to a draft; there is no real delete
        return devto_view(a)

    # ------------------------------------------------------------------ partner webhook receiver (our own protocol)
    def partner_denied(partner: str, raw: bytes, signature: str | None) -> JSONResponse | None:
        secret = state.partner_secrets.get(partner)
        if secret is None:
            return JSONResponse({"error": "unknown_partner"}, status_code=404)
        expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            return JSONResponse(_failure_body("partner", 401, None), status_code=401)
        return None

    @app.post("/partner/hooks/{partner}")
    async def partner_receive(partner: str, request: Request):
        raw = await request.body()
        if (denied := partner_denied(partner, raw, request.headers.get("X-IncuBrix-Signature"))) is not None:
            return denied
        payload = json.loads(raw)
        post_id = f"p{state.new_id()}"
        state.partner_posts.setdefault(partner, {})[post_id] = {
            "id": post_id, "payload": payload, "key": request.headers.get("X-IncuBrix-Idempotency-Key"), "deleted": False,
        }
        return JSONResponse({"id": post_id, "url": f"http://partner.mock/{partner}/{post_id}"}, status_code=201)

    @app.get("/partner/hooks/{partner}/posts/{post_id}")
    async def partner_get(partner: str, post_id: str, request: Request):
        if (denied := partner_denied(partner, b"", request.headers.get("X-IncuBrix-Signature"))) is not None:
            return denied
        post = state.partner_posts.get(partner, {}).get(post_id)
        if post is None or post["deleted"]:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return {"id": post_id, "url": f"http://partner.mock/{partner}/{post_id}", "status": "live"}

    @app.get("/partner/hooks/{partner}/by-key/{key}")
    async def partner_by_key(partner: str, key: str, request: Request):
        if (denied := partner_denied(partner, b"", request.headers.get("X-IncuBrix-Signature"))) is not None:
            return denied
        for post in state.partner_posts.get(partner, {}).values():
            if post["key"] == key and not post["deleted"]:
                return {"id": post["id"], "url": f"http://partner.mock/{partner}/{post['id']}", "status": "live"}
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.delete("/partner/hooks/{partner}/posts/{post_id}")
    async def partner_delete(partner: str, post_id: str, request: Request):
        if (denied := partner_denied(partner, b"", request.headers.get("X-IncuBrix-Signature"))) is not None:
            return denied
        post = state.partner_posts.get(partner, {}).get(post_id)
        if post is None or post["deleted"]:
            return JSONResponse({"error": "not_found"}, status_code=404)
        post["deleted"] = True
        return Response(status_code=204)

    # ------------------------------------------------------------------ Hashnode (modelled: one GraphQL endpoint)
    HASHNODE_PUBLICATION = "pub-demo"

    def gql_error(message: str, code: str) -> JSONResponse:
        # Hashnode answers HTTP 200 with an "errors" list for most failures: callers must read the body, not the status
        return JSONResponse({"errors": [{"message": message, "extensions": {"code": code}}], "data": None})

    def hashnode_view(post: dict[str, Any]) -> dict[str, Any]:
        return {"id": post["id"], "title": post["title"], "slug": post["slug"], "url": f"http://hashnode.mock/{post['slug']}",
                "views": post["views"], "reactionCount": post["reactionCount"], "responseCount": post["responseCount"],
                "canonicalUrl": post.get("canonicalUrl")}

    @app.post("/hashnode/graphql")
    async def hashnode_graphql(request: Request):
        if request.headers.get("Authorization") != state.hashnode_key:  # the token is sent as is, without "Bearer"
            return gql_error("You must be logged in to perform this action", "UNAUTHENTICATED")
        body = await request.json()
        op, variables = body.get("operationName"), body.get("variables") or {}
        if op == "PublishPost":
            data = variables.get("input") or {}
            title = str(data.get("title", "")).strip()
            if data.get("publicationId") != HASHNODE_PUBLICATION:
                return gql_error("Publication not found", "NOT_FOUND")
            if not title or len(title) < 6:
                return gql_error("Title must be at least 6 characters long", "BAD_USER_INPUT")
            if len(data.get("tags") or []) > 5:
                return gql_error("A post can have at most 5 tags", "BAD_USER_INPUT")
            post_id = f"hn{state.new_id():x}"
            state.hashnode_posts[post_id] = {"id": post_id, "title": title, "slug": f"{_slug(title)}-{post_id}", "views": 0, "reactionCount": 0, "responseCount": 0,
                                             "canonicalUrl": data.get("originalArticleURL"), "content": data.get("contentMarkdown", "")}
            return {"data": {"publishPost": {"post": hashnode_view(state.hashnode_posts[post_id])}}}
        if op == "GetPost":
            post = state.hashnode_posts.get(str(variables.get("id")))
            return {"data": {"post": hashnode_view(post) if post else None}}
        if op == "RemovePost":
            post = state.hashnode_posts.pop(str((variables.get("input") or {}).get("id")), None)
            if post is None:
                return gql_error("Post not found", "NOT_FOUND")
            return {"data": {"removePost": {"post": {"id": post["id"]}}}}
        if op == "ListPosts":
            return {"data": {"publication": {"posts": {"edges": [{"node": hashnode_view(p)} for p in reversed(list(state.hashnode_posts.values()))][: int(variables.get("first", 20))]}}}}
        return gql_error(f"Unknown operation {op!r}", "GRAPHQL_VALIDATION_FAILED")

    # ------------------------------------------------------------------ Kit (modelled, API v4 broadcasts)
    def kit_auth(request: Request) -> JSONResponse | None:
        if request.headers.get("X-Kit-Api-Key") != state.kit_key:
            return JSONResponse({"errors": ["The access token is invalid"]}, status_code=401)
        return None

    def kit_view(b: dict[str, Any]) -> dict[str, Any]:
        return {"id": b["id"], "subject": b["subject"], "content": b["content"], "public": b["public"], "send_at": b["send_at"], "published_at": b["published_at"],
                "created_at": b["created_at"], "preview_text": b.get("preview_text"), "description": b.get("description")}

    @app.post("/kit/v4/broadcasts")
    async def kit_create(request: Request):
        if (denied := kit_auth(request)) is not None:
            return denied
        data = await request.json()
        if not str(data.get("subject", "")).strip():
            return JSONResponse({"errors": ["Subject can't be blank"]}, status_code=422)
        broadcast_id = state.new_id()
        sent = bool(data.get("send_at")) and bool(data.get("public"))
        state.kit_broadcasts[broadcast_id] = {
            "id": broadcast_id, "subject": data["subject"], "content": data.get("content", ""), "public": bool(data.get("public", False)),
            "send_at": data.get("send_at"), "published_at": "2026-01-01T00:00:00Z" if sent else None, "created_at": "2026-01-01T00:00:00Z",
            "preview_text": data.get("preview_text"), "description": data.get("description"), "recipients": 0, "emails_opened": 0, "total_clicks": 0, "unsubscribes": 0,
        }
        return JSONResponse({"broadcast": kit_view(state.kit_broadcasts[broadcast_id])}, status_code=201)

    @app.get("/kit/v4/broadcasts")
    async def kit_list(request: Request):
        if (denied := kit_auth(request)) is not None:
            return denied
        return {"broadcasts": [kit_view(b) for b in reversed(list(state.kit_broadcasts.values()))], "pagination": {"has_next_page": False}}

    @app.get("/kit/v4/broadcasts/{broadcast_id}")
    async def kit_get(broadcast_id: int, request: Request):
        if (denied := kit_auth(request)) is not None:
            return denied
        b = state.kit_broadcasts.get(broadcast_id)
        if b is None:
            return JSONResponse({"errors": ["Not Found"]}, status_code=404)
        return {"broadcast": kit_view(b)}

    @app.get("/kit/v4/broadcasts/{broadcast_id}/stats")
    async def kit_stats(broadcast_id: int, request: Request):
        if (denied := kit_auth(request)) is not None:
            return denied
        b = state.kit_broadcasts.get(broadcast_id)
        if b is None:
            return JSONResponse({"errors": ["Not Found"]}, status_code=404)
        return {"broadcast": {"id": b["id"], "stats": {k: b[k] for k in ("recipients", "emails_opened", "total_clicks", "unsubscribes")}}}

    @app.delete("/kit/v4/broadcasts/{broadcast_id}")
    async def kit_delete(broadcast_id: int, request: Request):
        if (denied := kit_auth(request)) is not None:
            return denied
        if state.kit_broadcasts.pop(broadcast_id, None) is None:
            return JSONResponse({"errors": ["Not Found"]}, status_code=404)
        return Response(status_code=204)

    # ------------------------------------------------------------------ fake rules pages (for AI rules capture)
    RULES_PAGES = {
        "strict": "<html><body><h1>Community rules</h1><ul><li>Self-promotion is not allowed. Do not promote your own products.</li>"
                  "<li>You may post one post per week.</li><li>Do not post any links in the first comment or the body.</li>"
                  "<li>AI-generated content must be disclosed with the [AI] tag.</li><li>Bots are not allowed on this forum.</li></ul></body></html>",
        "relaxed": "<html><body><h1>Rules</h1><p>Be kind. Self-promotion is welcome if you take part in discussions. Up to 2 links per post. "
                   "Post as often as you like.</p></body></html>",
        "silent": "<html><body><h1>Welcome</h1><p>This is a friendly place to talk about video editing and creative tools.</p></body></html>",
    }

    @app.get("/files/catalogue.csv")
    async def fake_csv_file():
        rows = ["name,url,language,topics", "Remote One,http://mock-platforms:9100/sites/plain-r1,en,ai", "Remote Two,http://mock-platforms:9100/sites/discourse-r2,en,video"]
        return Response("\n".join(rows) + "\n", media_type="text/csv")

    @app.get("/rules/{name}")
    async def fake_rules_page(name: str):
        page = RULES_PAGES.get(name)
        return Response(page, media_type="text/html") if page else Response("not found", status_code=404)

    # ------------------------------------------------------------------ fake community websites (for the CSV import)
    def _members(name: str) -> int:
        return 100 + sum(ord(c) for c in name) * 7 % 9000  # a stable fake number per site

    @app.get("/sites/{name}/api/v3/site")
    async def fake_lemmy_stats(name: str):
        return {"site_view": {"counts": {"users": _members(name), "communities": 12}}}

    @app.get("/sites/{name}/about.json")
    async def fake_discourse_stats(name: str):
        return {"about": {"stats": {"users_count": _members(name), "topics_count": 300}}}

    @app.get("/sites/{name}/rules")
    async def fake_site_rules(name: str):
        return Response("<html><body><h1>Rules</h1><p>Be kind. Self-promotion is not allowed.</p></body></html>", media_type="text/html")

    @app.get("/sites/{name}")
    async def fake_site(name: str):
        """A website whose behaviour is chosen by its name, so import tests and the 1,000-row run need no real internet.
        dead-* -> 404 | gone-* -> 410 | boom-* -> 500 | slow-* -> answers after 0.3 s | discourse-* / ghost-* / lemmy-* -> says so
        in its generator tag | lang-xx-* -> <html lang=xx> | anything else -> an ordinary page."""
        if name.startswith("dead-"):
            return Response("not found", status_code=404)
        if name.startswith("gone-"):
            return Response("gone", status_code=410)
        if name.startswith("boom-"):
            return Response("error", status_code=500)
        if name.startswith("slow-"):
            await asyncio.sleep(0.3)
        lang = "en"
        if (m := re.match(r"lang-([a-z]{2})-", name)):
            lang = m.group(1)
        generator = {"discourse": "Discourse 3.2.0", "ghost": "Ghost 5.80", "lemmy": "Lemmy 0.19"}.get(name.split("-")[0], "")
        meta = f'<meta name="generator" content="{generator}">' if generator else ""
        html = (f'<!doctype html><html lang="{lang}"><head><title>Community {name}</title>{meta}'
                f'<meta name="description" content="A community called {name}."></head><body><h1>{name}</h1>'
                f'<p><a href="/sites/{name}/rules">Community rules</a></p></body></html>')
        return Response(html, media_type="text/html")

    return app


app = create_app()
