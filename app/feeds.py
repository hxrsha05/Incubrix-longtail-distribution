"""The MRSS (Media RSS) feeds this service hosts. Partners and aggregators poll a feed address; we add and remove items.

A feed item is identified by its guid. The feed connector derives the guid from the idempotency key, so adding the same
post twice is the same item and a timed-out publish can be checked exactly.
"""
from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ET
from email.utils import formatdate
from typing import Any, Callable

from sqlalchemy import delete, desc, insert, select
from sqlalchemy.engine import Engine

from app.db import feed_items as t
from app.errors import ErrorCode, IncuBrixError

MEDIA_NS = "http://search.yahoo.com/mrss/"
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("media", MEDIA_NS)
ET.register_namespace("atom", ATOM_NS)
_MEDIUM = {"image": "image", "video": "video", "file": "document"}


class FeedStore:
    def __init__(self, engine: Engine, max_items: int = 50, clock: Callable[[], float] = time.time) -> None:
        self.engine, self.max_items, self.clock = engine, max_items, clock
        self._last_stamp = 0.0
        self._stamp_lock = threading.Lock()

    def _next_stamp(self) -> float:
        """Strictly increasing, so two items added within one clock tick still have a clear order (newest first)."""
        with self._stamp_lock:
            self._last_stamp = max(self.clock(), self._last_stamp + 1e-6)
            return self._last_stamp

    def add(self, feed_id: str, item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Returns (item, created). The same guid again returns the existing item."""
        guid = item["guid"]
        existing = self.get(feed_id, guid, missing_ok=True)
        if existing is not None:
            return existing, False
        with self.engine.begin() as conn:
            conn.execute(insert(t).values(feed_id=feed_id, guid=guid, title=item["title"], description=item.get("description", ""), link=item.get("link"),
                                          canonical_url=item.get("canonical_url"), media=item.get("media") or [], tags=item.get("tags") or [], created_at=self._next_stamp()))
        return self.get(feed_id, guid), True

    def get(self, feed_id: str, guid: str, missing_ok: bool = False) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(t).where(t.c.feed_id == feed_id, t.c.guid == guid)).mappings().first()
        if row is None:
            if missing_ok:
                return None
            raise IncuBrixError(ErrorCode.NOT_FOUND, f"no item {guid!r} in feed {feed_id!r}")
        return dict(row)

    def remove(self, feed_id: str, guid: str) -> None:
        self.get(feed_id, guid)
        with self.engine.begin() as conn:
            conn.execute(delete(t).where(t.c.feed_id == feed_id, t.c.guid == guid))

    def items(self, feed_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(select(t).where(t.c.feed_id == feed_id).order_by(desc(t.c.created_at)).limit(limit or self.max_items)).mappings().all()
        return [dict(r) for r in rows]

    def render(self, feed_id: str, base_url: str) -> str:
        """The feed as MRSS (RSS 2.0 with the Media RSS namespace)."""
        rss = ET.Element("rss", {"version": "2.0"})
        channel = ET.SubElement(rss, "channel")
        self_url = f"{base_url.rstrip('/')}/feeds/{feed_id}.xml"
        ET.SubElement(channel, "title").text = f"IncuBrix feed: {feed_id}"
        ET.SubElement(channel, "link").text = self_url
        ET.SubElement(channel, "description").text = f"Content published through IncuBrix to the feed {feed_id}."
        ET.SubElement(channel, f"{{{ATOM_NS}}}link", {"href": self_url, "rel": "self", "type": "application/rss+xml"})
        for item in self.items(feed_id):
            node = ET.SubElement(channel, "item")
            ET.SubElement(node, "title").text = item["title"]
            ET.SubElement(node, "guid", {"isPermaLink": "false"}).text = item["guid"]
            ET.SubElement(node, "link").text = item["canonical_url"] or item["link"] or f"{self_url}#{item['guid']}"
            ET.SubElement(node, "description").text = item["description"] or ""
            ET.SubElement(node, "pubDate").text = formatdate(item["created_at"], usegmt=True)
            for tag in item["tags"] or []:
                ET.SubElement(node, "category").text = tag
            ET.SubElement(node, f"{{{MEDIA_NS}}}title").text = item["title"]
            if item["description"]:
                ET.SubElement(node, f"{{{MEDIA_NS}}}description").text = item["description"]
            for index, media in enumerate(item["media"] or []):
                attrs = {"url": media["url"], "medium": _MEDIUM.get(media.get("kind", "image"), "image")}
                if media.get("mime_type"):
                    attrs["type"] = media["mime_type"]
                ET.SubElement(node, f"{{{MEDIA_NS}}}content", attrs)
                if index == 0 and media.get("kind", "image") == "image":
                    ET.SubElement(node, f"{{{MEDIA_NS}}}thumbnail", {"url": media["url"]})
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode")
