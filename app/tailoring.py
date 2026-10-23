"""Tailoring (Advanced level): fit one piece of content to one destination, and say what was changed and why.

What it does, in this order:
  1. title and body are shortened at a WORD boundary (never in the middle of a word) to the destination's limits;
  2. links follow the destination's link policy (allowed | none | max:N); a removed link is reported;
  3. @mentions follow the destination's style (@name | u/name | removed);
  4. tags follow the destination's tag style: real tags (cleaned, limited), #hashtags added to the text, or none;
  5. the canonical URL (the original source) is set when the platform supports it, so search engines credit the original;
  6. an AI-disclosure line, when the destination requires one, is always present and is never the part that gets cut;
  7. a language difference between the content and the destination is reported (we do not translate).
It never changes meaning on its own: every change is listed in `changes`, anything it could not fix goes in `warnings`.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from app.models import Destination, Rendition

ELLIPSIS = "…"
URL = re.compile(r"https?://[^\s<>\"')\]]+")
MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_][A-Za-z0-9_.-]{0,38})")
DEFAULT_DISCLOSURE = "This post was created with the help of AI."
DISCUSSION_PROMPT = "What do you think? I would like to hear how others see this."


class TailoredResult(BaseModel):
    rendition: Rendition
    changes: list[str] = []
    warnings: list[str] = []


def truncate_words(text: str, limit: int) -> str:
    """Cut to at most `limit` characters at a word boundary, ending with one ellipsis character."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return ELLIPSIS
    cut = text[: limit - 1]
    if not text[limit - 1].isspace():  # we are inside a word: back up to the last space (a single enormous word is the only hard cut)
        space = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
        if space > 0:
            cut = cut[:space]
    return cut.rstrip(" \t\n.,;:-–—") + ELLIPSIS


def _limit(spec: dict[str, Any], key: str) -> int | None:
    value = spec.get(key)
    return int(value) if value is not None else None


def _clean_tag(tag: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tag.lower())


def tailor(rendition: Rendition, destination: Destination, spec: dict[str, Any], destination_language: str | None = None) -> TailoredResult:
    """`spec` is the destination's effective spec (the connector's surface spec with the destination's own overrides on top)."""
    r = rendition.model_copy(deep=True)
    changes: list[str] = []
    warnings: list[str] = []
    rules = destination.rules or {}

    # ---- links ----
    policy = rules.get("link_policy") or ("allowed" if spec.get("links_allowed", True) else "none")
    if spec.get("links_allowed") is False:
        policy = "none"
    allowed_links: int | None = None if policy == "allowed" else 0 if policy == "none" else _max_links(policy)
    if allowed_links is not None:
        urls = URL.findall(r.body)
        if r.link_url and r.link_url not in urls:
            urls = [r.link_url] + urls
        if len(urls) > allowed_links:
            keep = urls[:allowed_links]
            removed = [u for u in urls if u not in keep]
            for u in removed:
                r.body = r.body.replace(u, "").replace("  ", " ")
            r.body = re.sub(r"[ \t]+\n", "\n", r.body).strip()
            if r.link_url and r.link_url not in keep:
                r.link_url = None
            changes.append(f"removed {len(removed)} link(s): this destination allows {allowed_links}")

    # ---- mentions ----
    style = spec.get("mention_style", "at")
    if style == "u":
        new = MENTION.sub(r"u/\1", r.body)
    elif style == "none":
        new = re.sub(r"[ \t]{2,}", " ", MENTION.sub("", r.body)).strip()
    else:
        new = r.body
    if new != r.body:
        changes.append(f"mentions rewritten for this destination (style: {style})")
        r.body = new

    # ---- tags ----
    tag_style = spec.get("tag_style", "none")
    if tag_style == "tags":
        cleaned = list(dict.fromkeys(t for t in (_clean_tag(x) for x in r.tags) if t))
        if cleaned != r.tags:
            changes.append("tags cleaned to lower-case letters and digits, duplicates removed")
        max_tags = _limit(spec, "max_tags")
        if max_tags is not None and len(cleaned) > max_tags:
            changes.append(f"tags limited to {max_tags} (dropped {len(cleaned) - max_tags})")
            cleaned = cleaned[:max_tags]
        r.tags = cleaned
    elif tag_style == "hashtags":
        hashtags = list(dict.fromkeys(t for t in (_clean_tag(x) for x in r.tags) if t))[: _limit(spec, "hashtag_max") or 3]
        existing = {h.lower() for h in re.findall(r"#(\w+)", r.body)}
        extra = [f"#{h}" for h in hashtags if h not in existing]
        if extra:
            r.body = (r.body.rstrip() + "\n\n" + " ".join(extra)).strip()
            changes.append(f"added {len(extra)} hashtag(s) to the text: {' '.join(extra)}")
        r.tags = []
    else:  # none
        if r.tags:
            changes.append("tags dropped: this destination has no tags")
        r.tags = []

    # ---- discussion-first framing (forums) ----
    if spec.get("framing") == "discussion" and "?" not in r.body:
        r.body = (r.body.rstrip() + "\n\n" + DISCUSSION_PROMPT).strip()
        changes.append("closing question added: forum members answer questions, an announcement with no question gets few replies")

    # ---- canonical URL ----
    if spec.get("canonical_supported"):
        if not r.canonical_url and r.link_url:
            r.canonical_url = r.link_url
            changes.append("canonical URL set to the original link")
    elif r.canonical_url:
        changes.append("canonical URL dropped: this destination cannot use it")
        r.canonical_url = None

    # ---- AI disclosure: reserve room for it first, so it can never be the part that is cut ----
    disclosure = ""
    if rules.get("disclosure_required") and r.ai_generated:
        disclosure = r.ai_disclosure or DEFAULT_DISCLOSURE
        if r.ai_disclosure is None:
            r.ai_disclosure = disclosure
            changes.append("AI disclosure line added (required here)")
    body_without = r.body.replace(disclosure, "").strip() if disclosure else r.body

    # ---- length ----
    title_max, body_max = _limit(spec, "title_max_chars"), _limit(spec, "body_max_chars")
    if title_max is not None and len(r.title) > title_max:
        before = len(r.title)
        r.title = truncate_words(r.title.strip(), title_max)
        changes.append(f"title shortened at a word boundary: {before} -> {len(r.title)} characters (limit {title_max})")
    if body_max is not None:
        reserve = len(disclosure) + 2 if disclosure else 0
        full = f"{body_without}\n\n{disclosure}".strip() if disclosure else body_without
        if len(full) > body_max:
            if reserve >= body_max:
                warnings.append("the required AI disclosure alone is longer than the body limit; it was not cut, the post will be rejected")
                r.body = disclosure
            else:
                r.body = f"{truncate_words(body_without, body_max - reserve)}\n\n{disclosure}".strip() if disclosure else truncate_words(body_without, body_max)
                changes.append(f"body shortened at a word boundary to {len(r.body)} characters (limit {body_max})" + (", AI disclosure kept" if disclosure else ""))
        else:
            r.body = full

    # ---- language ----
    if destination_language and r.language and destination_language.split("-")[0] != r.language.split("-")[0]:
        warnings.append(f"the content is in {r.language!r} but the destination is {destination_language!r}; it was not translated")

    # ---- things only a person can settle ----
    if rules.get("self_promo_allowed") is False and r.promotional:
        warnings.append("the content is promotional and this destination does not allow self-promotion (the rules engine will block it)")
    return TailoredResult(rendition=r, changes=changes, warnings=warnings)


def _max_links(policy: str) -> int:
    match = re.fullmatch(r"max:(\d+)", str(policy))
    return int(match.group(1)) if match else 0  # an unknown policy is treated as the strictest
