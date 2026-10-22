"""Assisted connector: for destinations that have no API (or forbid automation).

It never posts. build_assist_pack() prepares everything a person needs to post by hand in under a minute: the media
files, a caption that already respects the destination's limits, a deep link straight to the submit page, and a
checklist of that destination's rules. publish/delete are deliberately not offered (the manifest lists only "assist").
"""
from __future__ import annotations

from urllib.parse import quote

from app.connectors.base import BaseConnector
from app.connectors.registry import register_connector
from app.errors import ErrorCode, IncuBrixError
from app.models import AssistPack, Destination, Rendition


def _fit(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"  # one ellipsis character, so the result is exactly within the limit


@register_connector("assisted")
class AssistedConnector(BaseConnector):
    def build_assist_pack(self, rendition: Rendition, destination: Destination) -> AssistPack:
        submit_url = destination.extra.get("submit_url") or destination.base_url
        if not submit_url:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "assisted destination needs extra.submit_url or base_url")
        surface = self.manifest.surface(destination.surface) if destination.surface else self.manifest.surfaces[0]
        spec = {**(surface.spec if surface else {}), **destination.spec}
        title = _fit(rendition.title, spec.get("title_max_chars"))
        parts = [title]
        if rendition.body:
            parts.append(rendition.body)
        if rendition.link_url and spec.get("links_allowed", True) is not False:
            parts.append(rendition.link_url)
        if rendition.ai_generated and rendition.ai_disclosure and rendition.ai_disclosure not in rendition.body:
            parts.append(rendition.ai_disclosure)
        caption = _fit("\n\n".join(parts), spec.get("body_max_chars"))
        deep_link = submit_url
        if destination.extra.get("prefill_param"):  # some sites accept ?title=... on their submit page
            sep = "&" if "?" in submit_url else "?"
            deep_link = f"{submit_url}{sep}{destination.extra['prefill_param']}={quote(title)}"
        checklist = [f"Open {deep_link}", "Paste the caption", "Attach the files listed"]
        if destination.rules.get("disclosure_required"):
            checklist.append("Confirm the AI disclosure line is in the post")
        if destination.rules.get("self_promo_allowed") is False:
            checklist.append("Do not post promotional content here")
        if destination.rules.get("link_policy") == "none":
            checklist.append("Leave links out of the post; this destination forbids them")
        freq = destination.rules.get("frequency_cap")
        if freq:
            checklist.append(f"Respect the posting limit for this destination: {freq}")
        checklist.append("Copy the live post link back into IncuBrix")
        return AssistPack(files=[m.url for m in rendition.media], caption=caption, deep_link=deep_link, checklist=checklist)
