"""Hashnode connector (GraphQL API). One connection = one Hashnode publication (a blog).

  endpoint     connection.base_url (default https://gql.hashnode.com)
  auth         Authorization header = the personal access token, sent as is (no "Bearer")
  publication  destination.community or destination.extra["publication_id"]

Hashnode answers HTTP 200 with an "errors" list for most failures, so the body is always checked: the error code inside it
is mapped to the shared error codes. HONESTY NOTE: this was built against Hashnode's documented API and a mock; it has not
been run against a real Hashnode account.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.connectors.base import BaseConnector, markdown_images
from app.connectors.registry import register_connector
from app.errors import ErrorCode, IncuBrixError
from app.models import Connection, DeleteResult, Destination, MetricsResult, PublishResult, Rendition, StatusResult

DEFAULT_ENDPOINT = "https://gql.hashnode.com"
POST_FIELDS = "id title slug url views reactionCount responseCount canonicalUrl"

PUBLISH = "mutation PublishPost($input: PublishPostInput!) { publishPost(input: $input) { post { " + POST_FIELDS + " } } }"
GET_POST = "query GetPost($id: ID!) { post(id: $id) { " + POST_FIELDS + " } }"
REMOVE = "mutation RemovePost($input: RemovePostInput!) { removePost(input: $input) { post { id } } }"
LIST_POSTS = "query ListPosts($id: ObjectId!, $first: Int!) { publication(id: $id) { posts(first: $first) { edges { node { " + POST_FIELDS + " } } } } }"

_AUTH_CODES = {"UNAUTHENTICATED", "FORBIDDEN", "UNAUTHORIZED"}
_REJECT_CODES = {"BAD_USER_INPUT", "GRAPHQL_VALIDATION_FAILED"}


def _tag(tag: str) -> dict[str, str]:
    slug = re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")
    return {"slug": slug, "name": tag.strip()[:30]}


def _markdown(rendition: Rendition) -> str:
    body = rendition.body
    if rendition.link_url and rendition.link_url not in body:
        body = f"{body}\n\n{rendition.link_url}".strip()
    images = markdown_images(rendition)
    return f"{body}\n\n{images}".strip() if images else body


@register_connector("hashnode")
class HashnodeConnector(BaseConnector):
    def publish(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult:
        data: dict[str, Any] = {"title": rendition.title, "contentMarkdown": _markdown(rendition), "publicationId": self._publication(destination)}
        tags = [t for t in (_tag(x) for x in rendition.tags) if t["slug"]]
        if tags:
            data["tags"] = tags
        if rendition.canonical_url:
            data["originalArticleURL"] = rendition.canonical_url
        post = self._gql("PublishPost", PUBLISH, {"input": data}, connection)["publishPost"]["post"]
        return self._result(post)

    def reconcile(self, rendition: Rendition, destination: Destination, connection: Connection, idempotency_key: str) -> PublishResult | None:
        """Did an earlier timed-out attempt create the post? Look through the publication's newest posts for the same title."""
        edges = self._gql("ListPosts", LIST_POSTS, {"id": self._publication(destination), "first": 50}, connection)["publication"]["posts"]["edges"]
        for edge in edges:
            if edge["node"]["title"] == rendition.title:
                return self._result(edge["node"])
        return None

    def get_status(self, platform_post_id: str, connection: Connection) -> StatusResult:
        post = self._gql("GetPost", GET_POST, {"id": platform_post_id}, connection)["post"]
        if post is None:
            return StatusResult(status="not_found", live_url=None)
        return StatusResult(status="live", live_url=post["url"])

    def delete(self, platform_post_id: str, connection: Connection) -> DeleteResult:
        try:
            self._gql("RemovePost", REMOVE, {"input": {"id": platform_post_id}}, connection)
        except IncuBrixError as exc:
            if exc.details.get("platform_code") != "NOT_FOUND":
                raise  # not found means it is already gone
        return DeleteResult(deleted=True)

    def fetch_metrics(self, platform_post_id: str, connection: Connection, since: str | None) -> MetricsResult:
        post = self._gql("GetPost", GET_POST, {"id": platform_post_id}, connection)["post"]
        if post is None:
            raise IncuBrixError(ErrorCode.NOT_FOUND, "post not found")
        return MetricsResult(views=post["views"], likes=post["reactionCount"], comments=post["responseCount"])

    # ---------- helpers ----------
    @staticmethod
    def _publication(destination: Destination) -> str:
        pub = destination.community or destination.extra.get("publication_id")
        if not pub:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "hashnode destination needs a publication id (community or extra.publication_id)")
        return str(pub)

    @staticmethod
    def _result(post: dict[str, Any]) -> PublishResult:
        return PublishResult(platform_post_id=post["id"], live_url=post["url"], status="live")

    def _gql(self, operation: str, query: str, variables: dict[str, Any], connection: Connection) -> dict[str, Any]:
        if not connection.api_key:
            raise IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "connection has no Hashnode access token")
        url = (connection.base_url or DEFAULT_ENDPOINT).rstrip("/")
        body = self._call("POST", url, headers={"Authorization": connection.api_key.get_secret_value()},
                          json={"operationName": operation, "query": query, "variables": variables}).json()
        if body.get("errors"):
            raise self._graphql_error(body["errors"][0])
        return body["data"]  # a missing "data" is a KeyError on purpose: certification reads it as "the platform changed its answer"

    @staticmethod
    def _graphql_error(error: dict[str, Any]) -> IncuBrixError:
        code = (error.get("extensions") or {}).get("code", "")
        message = str(error.get("message", ""))[:200]
        details = {"platform_code": code, "platform_message": message}
        if code in _AUTH_CODES:
            return IncuBrixError(ErrorCode.CONNECTION_AUTH_EXPIRED, "Hashnode rejected the access token", details)
        if code in _REJECT_CODES:
            return IncuBrixError(ErrorCode.PLATFORM_CONTENT_REJECTED, f"Hashnode refused the post: {message}", details)
        if code == "NOT_FOUND":
            return IncuBrixError(ErrorCode.NOT_FOUND, message or "not found", details)
        if "rate" in code.lower() or "rate limit" in message.lower():
            return IncuBrixError(ErrorCode.PLATFORM_RATE_LIMIT, "Hashnode asked us to slow down", details)
        return IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"Hashnode reported an error: {message}", {**details, "outcome_unknown": True})
