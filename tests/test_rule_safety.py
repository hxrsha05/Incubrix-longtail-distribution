"""Rule safety on the large independent test set (scripts/eval_rules.py): no violation may be missed, no legal post may be stopped."""
from scripts.eval_rules import evaluate


def test_no_violation_is_missed_and_no_legal_post_is_blocked():
    r = evaluate()
    assert r["cases"] > 10_000
    assert r["missed"] == 0, r["problems"][:3]
    assert r["wrong_action_or_reason"] == 0, r["problems"][:3]
    assert r["false_blocks"] == 0, r["problems"][:3]
    assert r["caught"] == r["violating_cases"] and set(r["by_kind"]) == {"automation", "self_promotion", "link_policy", "ai_disclosure", "frequency_cap", "rules_missing"}
