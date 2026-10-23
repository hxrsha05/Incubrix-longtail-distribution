import pytest

from app.models import Rendition
from app.rules import check_rules

OPEN = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}
NOW = 1_000_000.0
WEEK = 7 * 24 * 3600


def run(rendition=None, rules=None, history=(), automated=True):
    rendition = rendition or Rendition(title="t")
    merged = {**OPEN, **(rules or {})}
    return check_rules(rendition, merged, now=NOW, automated=automated, post_times_since=lambda since: [h for h in history if h > since])


def rules_hit(decision):
    return {v.rule for v in decision.violations}


def test_open_rules_allow():
    assert run().allowed


def test_missing_rule_keys_fail_closed():
    d = check_rules(Rendition(title="t"), {"link_policy": "allowed"}, now=NOW, automated=True, post_times_since=lambda s: [])
    assert d.action == "block" and rules_hit(d) == {"rules_missing"}


def test_promo_to_no_promo_destination_is_blocked():
    d = run(Rendition(title="t", promotional=True), {"self_promo_allowed": False})
    assert d.action == "block" and rules_hit(d) == {"self_promotion"}


def test_non_promo_to_no_promo_destination_is_fine():
    assert run(Rendition(title="t", promotional=False), {"self_promo_allowed": False}).allowed


def test_second_post_inside_weekly_cap_is_rescheduled():
    cap = {"frequency_cap": {"max_posts": 1, "per_seconds": WEEK}}
    first_post = NOW - 3600  # one hour ago
    d = run(rules=cap, history=[first_post])
    assert d.action == "reschedule"
    assert d.reschedule_at == first_post + WEEK  # the slot opens exactly when the first post ages out


def test_post_outside_the_cap_window_is_allowed():
    cap = {"frequency_cap": {"max_posts": 1, "per_seconds": WEEK}}
    assert run(rules=cap, history=[NOW - WEEK - 1]).allowed


def test_cap_of_two_reschedules_for_the_right_slot():
    cap = {"frequency_cap": {"max_posts": 2, "per_seconds": 1000}}
    d = run(rules=cap, history=[NOW - 900, NOW - 100])
    assert d.action == "reschedule" and d.reschedule_at == NOW - 900 + 1000


@pytest.mark.parametrize(
    "policy,body,link,hit",
    [
        ("none", "no links here", None, False),
        ("none", "see https://a.example", None, True),
        ("none", "", "https://a.example", True),
        ("max:1", "https://a.example and https://b.example", None, True),
        ("max:1", "https://a.example", None, False),
        ("allowed", "https://a.example https://b.example", "https://c.example", False),
    ],
)
def test_link_policy(policy, body, link, hit):
    d = run(Rendition(title="t", body=body, link_url=link), {"link_policy": policy})
    assert (rules_hit(d) == {"link_policy"}) is hit
    assert d.allowed is (not hit)


def test_ai_disclosure_required():
    rules = {"disclosure_required": True}
    assert rules_hit(run(Rendition(title="t", body="hi", ai_generated=True), rules)) == {"ai_disclosure"}
    ok = Rendition(title="t", body="hi\n\nAI-generated.", ai_generated=True, ai_disclosure="AI-generated.")
    assert run(ok, rules).allowed
    assert run(Rendition(title="t", body="hi", ai_generated=False), rules).allowed  # nothing to disclose


def test_automation_not_allowed_blocks_automated_but_not_assisted():
    rules = {"automation_allowed": False}
    assert rules_hit(run(rules=rules, automated=True)) == {"automation"}
    assert run(rules=rules, automated=False).allowed


def test_hard_violation_beats_reschedule():
    cap = {"frequency_cap": {"max_posts": 1, "per_seconds": WEEK}, "self_promo_allowed": False}
    d = run(Rendition(title="t", promotional=True), cap, history=[NOW - 10])
    assert d.action == "block"  # waiting a week would not make a banned post allowed


# ---------------------------------------------------------------- links in any form, and adverts that are not marked
import pytest as _pytest  # noqa: E402

from app.models import Rendition as _R  # noqa: E402
from app.rules import check_rules as _check, count_links as _count  # noqa: E402

NO_LINKS = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "none", "disclosure_required": False, "automation_allowed": True}
NO_PROMO = {**NO_LINKS, "link_policy": "allowed", "self_promo_allowed": False}


def _decide(rendition, rules):
    return _check(rendition, rules, now=1000.0, automated=True, post_times_since=lambda since: [])


@_pytest.mark.parametrize("body,expected", [
    ("see https://example.com/page", 1), ("see HTTP://EXAMPLE.COM/Page", 1), ("visit www.example.com today", 1), ("read example.com/blog/post now", 1),
    ("[docs](https://docs.example.org/x)", 1), ('<a href="http://y.example/z">here</a>', 1), ("two: example.com and other.io/a", 2),
    ("the same one twice: https://example.com/a/ and example.com/a", 1), ("no links at all, just words", 0), ("mail me@example.com please", 0),
    ("Node.js version 1.2.3 and e.g. this", 0), ("Thanks.So we begin", 0), ("a trailing dot https://example.com/a.", 1),
])
def test_links_are_counted_in_every_form_people_write_them(body, expected):
    assert _count(_R(title="t", body=body)) == expected


@_pytest.mark.parametrize("body", ["check www.example.com", "go to example.com/offer", "[here](https://x.example)", "see https://x.example"])
def test_a_no_links_destination_blocks_a_link_however_it_is_written(body):
    d = _decide(_R(title="t", body=body), NO_LINKS)
    assert d.action == "block" and d.violations[0].rule == "link_policy"


def test_an_advert_that_was_not_marked_promotional_is_still_caught_where_promotion_is_banned():
    d = _decide(_R(title="Big news", body="Buy now and get 20% off with our discount code, a limited time offer", promotional=False), NO_PROMO)
    assert d.action == "block" and d.violations[0].rule == "self_promotion" and "looks promotional" in d.violations[0].reason


def test_a_single_marker_is_not_enough_and_ordinary_posts_pass():
    assert _decide(_R(title="Our free trial results", body="We wrote up what we learned running a study."), NO_PROMO).action == "allow"
    assert _decide(_R(title="How we edit video", body="A walkthrough of our editing workflow, with tips."), NO_PROMO).action == "allow"


def test_the_promo_heuristic_only_applies_where_promotion_is_banned():
    assert _decide(_R(title="t", body="Buy now, 50% off, use code SAVE"), {**NO_PROMO, "self_promo_allowed": True}).action == "allow"
