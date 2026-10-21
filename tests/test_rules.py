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
