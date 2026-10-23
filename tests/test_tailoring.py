"""Tailoring: word-boundary truncation, link policy, mentions, tags, canonical URL, AI disclosure, language."""
import httpx
import pytest

from app.connectors.manifest import load_manifest
from app.connectors.registry import DEFAULT_MANIFEST_DIR
from app.models import Destination, Rendition
from app.tailoring import ELLIPSIS, tailor, truncate_words


def spec_for(key, **overrides):
    manifest = load_manifest(DEFAULT_MANIFEST_DIR / f"{key}.yaml")
    return {**manifest.surfaces[0].spec, **overrides}


def dest(key="devto", **kw):
    return Destination(name="d", connector_key=key, **kw)


# ---------------------------------------------------------------- truncation
def test_text_that_fits_is_untouched():
    assert truncate_words("hello world", 11) == "hello world"


def test_truncation_never_cuts_a_word_in_half_and_stays_within_the_limit():
    text = "The quick brown fox jumps over the lazy dog"
    words = set(text.split())
    for limit in range(5, len(text)):  # from 5: the first word ("The" + the ellipsis) can fit; below that a hard cut is the only option
        out = truncate_words(text, limit)
        assert len(out) <= limit and out.endswith(ELLIPSIS)
        assert set(out[:-1].split()) <= words, (limit, out)  # only whole words survive


def test_truncation_example():
    assert truncate_words("The quick brown fox jumps over the lazy dog", 20) == "The quick brown fox" + ELLIPSIS


def test_one_enormous_word_is_cut_hard_rather_than_dropped():
    out = truncate_words("x" * 100, 10)
    assert out == "x" * 9 + ELLIPSIS


def test_trailing_punctuation_is_not_left_before_the_ellipsis():
    assert truncate_words("Hello, world, this is long", 14) == "Hello, world" + ELLIPSIS


# ---------------------------------------------------------------- tailoring
def test_long_title_is_shortened_at_a_word_and_reported():
    r = Rendition(title="Ten practical ways to make your short videos easier to find and share " * 3, body="b")
    out = tailor(r, dest(), spec_for("devto"))
    assert len(out.rendition.title) <= 128 and out.rendition.title.endswith(ELLIPSIS)
    assert any("title shortened" in c for c in out.changes)


def test_body_over_the_limit_is_shortened_and_the_ai_disclosure_survives():
    disclosure = "Written with AI help."
    r = Rendition(title="T", body="word " * 400, ai_generated=True, ai_disclosure=disclosure)
    out = tailor(r, dest(rules={"disclosure_required": True}), spec_for("devto", body_max_chars=200))
    assert len(out.rendition.body) <= 200 and out.rendition.body.endswith(disclosure)
    assert any("AI disclosure kept" in c for c in out.changes)


def test_missing_disclosure_line_is_added_when_required():
    out = tailor(Rendition(title="T", body="hello", ai_generated=True), dest(rules={"disclosure_required": True}), spec_for("devto"))
    assert out.rendition.ai_disclosure and out.rendition.body.endswith(out.rendition.ai_disclosure)
    assert any("AI disclosure line added" in c for c in out.changes)


def test_disclosure_is_not_added_for_human_written_content_or_when_not_required():
    assert tailor(Rendition(title="T", body="hi"), dest(rules={"disclosure_required": True}), spec_for("devto")).rendition.body == "hi"
    assert tailor(Rendition(title="T", body="hi", ai_generated=True), dest(rules={"disclosure_required": False}), spec_for("devto")).rendition.body == "hi"


def test_a_disclosure_longer_than_the_limit_is_flagged_not_cut():
    out = tailor(Rendition(title="T", body="x", ai_generated=True, ai_disclosure="d" * 50), dest(rules={"disclosure_required": True}), spec_for("devto", body_max_chars=20))
    assert out.warnings and "disclosure" in out.warnings[0] and out.rendition.body == "d" * 50


def test_link_policy_none_removes_every_link():
    r = Rendition(title="T", body="See https://a.example/x and https://b.example/y for more", link_url="https://c.example")
    out = tailor(r, dest(rules={"link_policy": "none"}), spec_for("devto"))
    assert "http" not in out.rendition.body and out.rendition.link_url is None
    assert any("removed 3 link" in c for c in out.changes)


def test_link_policy_max_keeps_the_first_n():
    r = Rendition(title="T", body="One https://a.example two https://b.example three https://c.example")
    out = tailor(r, dest(rules={"link_policy": "max:1"}), spec_for("devto"))
    assert out.rendition.body.count("http") == 1 and "https://a.example" in out.rendition.body


def test_link_policy_allowed_changes_nothing():
    r = Rendition(title="T", body="See https://a.example/x")
    assert tailor(r, dest(rules={"link_policy": "allowed"}), spec_for("devto")).rendition.body == r.body


@pytest.mark.parametrize("style,expected", [("at", "thanks @alice and @bob"), ("u", "thanks u/alice and u/bob"), ("none", "thanks and")])
def test_mention_styles(style, expected):
    out = tailor(Rendition(title="T", body="thanks @alice and @bob"), dest(), spec_for("devto", mention_style=style))
    assert out.rendition.body == expected


def test_an_email_address_is_not_a_mention():
    assert tailor(Rendition(title="T", body="mail me@example.com"), dest(), spec_for("devto", mention_style="u")).rendition.body == "mail me@example.com"


def test_real_tags_are_cleaned_deduplicated_and_limited():
    r = Rendition(title="T", body="b", tags=["AI", "Video Tools", "ai", "x-y", "d", "e"])
    out = tailor(r, dest(), spec_for("devto"))
    assert out.rendition.tags == ["ai", "videotools", "xy", "d"]
    assert any("limited to 4" in c for c in out.changes)


def test_hashtag_style_adds_hashtags_to_the_text_and_clears_tags():
    r = Rendition(title="T", body="Launch day", tags=["AI", "Video Tools", "indie", "extra"])
    out = tailor(r, dest("assisted"), spec_for("assisted"))
    assert out.rendition.body == "Launch day\n\n#ai #videotools #indie" and out.rendition.tags == []


def test_hashtags_already_in_the_text_are_not_repeated():
    out = tailor(Rendition(title="T", body="Launch #ai", tags=["ai", "video"]), dest("assisted"), spec_for("assisted"))
    assert out.rendition.body.count("#ai") == 1 and "#video" in out.rendition.body


def test_tags_are_dropped_where_the_destination_has_none():
    out = tailor(Rendition(title="T", body="b", tags=["ai"]), dest("lemmy"), spec_for("lemmy"))
    assert out.rendition.tags == [] and any("tags dropped" in c for c in out.changes)


def test_canonical_url_is_set_where_supported_and_dropped_where_not():
    r = Rendition(title="T", body="b", link_url="https://site.example/original")
    assert tailor(r, dest("devto"), spec_for("devto")).rendition.canonical_url == "https://site.example/original"
    kept = tailor(Rendition(title="T", body="b", canonical_url="https://x.example/o"), dest("devto"), spec_for("devto")).rendition
    assert kept.canonical_url == "https://x.example/o"
    gone = tailor(Rendition(title="T", body="b", canonical_url="https://x.example/o"), dest("lemmy"), spec_for("lemmy"))
    assert gone.rendition.canonical_url is None and any("canonical URL dropped" in c for c in gone.changes)


def test_language_difference_is_a_warning_and_text_is_not_translated():
    out = tailor(Rendition(title="Hallo", body="Welt", language="de"), dest(), spec_for("devto"), destination_language="en")
    assert out.rendition.body == "Welt" and "not translated" in out.warnings[0]
    assert not tailor(Rendition(title="T", body="b"), dest(), spec_for("devto"), destination_language="en-GB").warnings


def test_promotion_to_a_no_promo_destination_is_warned_about():
    out = tailor(Rendition(title="Buy", body="b", promotional=True), dest(rules={"self_promo_allowed": False}), spec_for("devto"))
    assert "does not allow self-promotion" in out.warnings[0]


def test_the_original_is_never_modified():
    r = Rendition(title="x " * 300, body="y " * 5000, tags=["AI", "AI"])
    before = r.model_dump()
    tailor(r, dest(), spec_for("devto"))
    assert r.model_dump() == before


def test_result_always_fits_every_connectors_limits():
    """The point of tailoring: whatever comes out passes the connector's own validate()."""
    from app.connectors.registry import build_registry

    registry = build_registry(httpx.Client(), set())
    content = Rendition(title="A very long title " * 40, body="Some long body text with a link https://x.example and @someone " * 900, tags=["AI", "a b", "c"],
                        link_url="https://x.example/orig", ai_generated=True)
    for key in sorted(registry.keys()):
        connector = registry.get(key)
        d = Destination(name=f"d-{key}", connector_key=key, rules={"disclosure_required": True, "link_policy": "max:1"})
        out = tailor(content, d, connector.effective_spec(d))
        verdict = connector.validate(out.rendition, d, None)
        assert verdict.ok, (key, verdict.violations)
