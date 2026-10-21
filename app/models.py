"""Request/response shapes for the connector contract (brief Appendix A).

Terms:
  rendition   - the content to post (title, text, link, media)
  destination - one catalogue row (which community / site, its limits and rules)
  connection  - the credentials + instance address used to talk to the platform
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr


class Media(BaseModel):
    url: str
    kind: Literal["image", "video", "file"] = "image"
    mime_type: str | None = None


class Rendition(BaseModel):
    title: str
    body: str = ""
    link_url: str | None = None
    canonical_url: str | None = None
    media: list[Media] = []
    tags: list[str] = []
    content_type: str = "text"  # text | image | video_link | article
    language: str = "en"
    promotional: bool = False
    ai_generated: bool = False
    ai_disclosure: str | None = None  # the disclosure line, which must also appear in body when a destination requires it


class Destination(BaseModel):
    name: str
    connector_key: str
    surface: str = "topic"
    base_url: str | None = None
    community: str | None = None  # forum category / lemmy community / etc.
    spec: dict[str, Any] = Field(default_factory=dict)  # per-destination overrides of the manifest spec
    rules: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class Connection(BaseModel):
    """Credentials are SecretStr so they print as '**********' even if something logs the object."""

    base_url: str | None = None
    api_key: SecretStr | None = None
    token: SecretStr | None = None
    username: str | None = None
    password: SecretStr | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------- results ----------
class Violation(BaseModel):
    field: str
    reason: str
    limit: int | None = None
    actual: int | None = None


class ValidateResult(BaseModel):
    ok: bool
    violations: list[Violation] = []


class PublishResult(BaseModel):
    platform_post_id: str
    live_url: str
    status: str


class StatusResult(BaseModel):
    status: str
    live_url: str | None = None


class DeleteResult(BaseModel):
    deleted: bool


class MetricsResult(BaseModel):
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    watch_time_s: float = 0.0
    clicks: int = 0


class RefreshResult(BaseModel):
    expires_at: str | None = None


class AssistPack(BaseModel):
    files: list[str] = []
    caption: str
    deep_link: str
    checklist: list[str] = []


# ---------- requests ----------
class ValidateRequest(BaseModel):
    rendition: Rendition
    destination: Destination
    connection: Connection


class PublishRequest(ValidateRequest):
    idempotency_key: str = Field(min_length=1)


class StatusRequest(BaseModel):
    platform_post_id: str
    connection: Connection


class DeleteRequest(StatusRequest):
    pass


class MetricsRequest(StatusRequest):
    since: str | None = None


class RefreshAuthRequest(BaseModel):
    connection: Connection


class AssistPackRequest(BaseModel):
    rendition: Rendition
    destination: Destination
