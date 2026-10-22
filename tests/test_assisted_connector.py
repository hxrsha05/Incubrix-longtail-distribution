"""The assisted connector prepares a pack for a person; it never posts."""
import httpx
import pytest

from app.connectors.assisted import AssistedConnector
from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR
from app.errors import IncuBrixError
from app.models import Destination, Media, Rendition


def make():
    return AssistedConnector(load_manifest(DEFAULT_MANIFEST_DIR / "assisted.yaml"), httpx.Client())


def dest(**kw):
    return Destination(name="site", connector_key="assisted", extra={"submit_url": "https://forum.example/submit"}, **kw)


def test_pack_has_files_caption_deep_link_and_checklist():
    post = Rendition(title="Hello", body="Body text", link_url="https://x.example/a", media=[Media(url="https://cdn/a.png")])
    pack = make().build_assist_pack(post, dest())
    assert pack.files == ["https://cdn/a.png"] and pack.deep_link == "https://forum.example/submit"
    assert "Hello" in pack.caption and "Body text" in pack.caption and "https://x.example/a" in pack.caption
    assert pack.checklist[0].startswith("Open") and pack.checklist[-1].startswith("Copy the live post link")


def test_caption_and_title_respect_the_destination_limits():
    pack = make().build_assist_pack(Rendition(title="T" * 50, body="b" * 500), dest(spec={"title_max_chars": 20, "body_max_chars": 100}))
    assert len(pack.caption) <= 100 and pack.caption.startswith("T" * 19 + "…")


def test_destination_rules_show_up_in_the_checklist_and_links_can_be_dropped():
    rules = {"disclosure_required": True, "self_promo_allowed": False, "link_policy": "none", "frequency_cap": "1 per week"}
    pack = make().build_assist_pack(Rendition(title="Hello", link_url="https://x.example/a"), dest(rules=rules, spec={"links_allowed": False}))
    text = " ".join(pack.checklist)
    assert "disclosure" in text and "promotional" in text and "forbids them" in text and "1 per week" in text
    assert "https://x.example/a" not in pack.caption


def test_ai_disclosure_is_added_to_the_caption_when_missing():
    pack = make().build_assist_pack(Rendition(title="Hello", ai_generated=True, ai_disclosure="Made with AI."), dest())
    assert pack.caption.endswith("Made with AI.")


def test_prefill_parameter_builds_the_deep_link():
    pack = make().build_assist_pack(Rendition(title="Hello world"), dest().model_copy(update={"extra": {"submit_url": "https://f.example/new", "prefill_param": "title"}}))
    assert pack.deep_link == "https://f.example/new?title=Hello%20world"


def test_destination_without_an_address_is_a_validation_error():
    with pytest.raises(IncuBrixError) as exc:
        make().build_assist_pack(Rendition(title="x"), Destination(name="s", connector_key="assisted"))
    assert exc.value.code.value == "VALIDATION_ERROR"


def test_it_cannot_publish():
    assert make().manifest.supports("publish") is False and make().manifest.supports("build_assist_pack") is True
