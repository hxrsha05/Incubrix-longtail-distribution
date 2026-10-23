"""Measure rule safety: how many rule violations does the engine catch, and how many does it miss?   python -m scripts.eval_rules

The test set is every combination of the five rules and the content that could break them (a few thousand cases). The EXPECTED answer for
each comes from a small oracle in this file, written from the rules as the brief states them, NOT by calling the engine. So a miss means
the engine let through something the rules forbid, and a false block means it stopped something the rules allow.

Violation kinds and the expected decision:
  automation      automated posting where automation_allowed is false (the assisted connector never posts, so it is exempt)  -> block
  self_promotion  a promotional post where self-promotion is banned                                                        -> block
  link_policy     any link where the policy is "none"; more links than N where it is "max:N"                              -> block
  ai_disclosure   AI-generated content without its disclosure line where disclosure is required                           -> block
  frequency_cap   a post when the cap is already used up in the window (and nothing else is wrong)                        -> reschedule
  rules_missing   a destination whose rules are incomplete                                                                 -> block (fail closed)
A hard violation beats a reschedule. Writes test-pack/rule-safety/report.md and report.json.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

from app.models import Rendition
from app.rules import REQUIRED_RULE_KEYS, check_rules

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_000_000.0
WEEK = 604_800.0
DISCLOSURE = "Made with AI."

LINK_BODIES = {  # how many links, and how they are written
    0: ("Plain words with no address at all.", None),
    1: ("Read more at https://example.com/post for details.", None),
    "1-bare": ("Read more at example.com/post for details.", None),
    "1-www": ("Read more at www.example.org today.", None),
    "1-field": ("Read the post.", "https://example.com/post"),
    2: ("See https://example.com/a and https://example.org/b.", None),
}
LINK_COUNT = {0: 0, 1: 1, "1-bare": 1, "1-www": 1, "1-field": 1, 2: 2}


def oracle(case: dict) -> tuple[str, set[str]]:
    """The expected decision and the violated rules, from the rules as written."""
    rules, content = case["rules"], case["content"]
    if any(k not in rules for k in REQUIRED_RULE_KEYS):
        return "block", {"rules_missing"}
    hard: set[str] = set()
    if case["automated"] and rules["automation_allowed"] is False:
        hard.add("automation")
    if content["promotional"] and rules["self_promo_allowed"] is False:
        hard.add("self_promotion")
    policy, links = rules["link_policy"], LINK_COUNT[content["links"]]
    if (policy == "none" and links > 0) or (policy.startswith("max:") and links > int(policy[4:])):
        hard.add("link_policy")
    if rules["disclosure_required"] and content["ai"] and not content["disclosure_line"]:
        hard.add("ai_disclosure")
    if hard:
        return "block", hard
    cap = rules["frequency_cap"]
    if cap and case["recent_posts"] >= cap["max_posts"]:
        return "reschedule", {"frequency_cap"}
    return "allow", set()


def cases():
    caps = [None, {"max_posts": 1, "per_seconds": WEEK}, {"max_posts": 3, "per_seconds": 86400}]
    for cap, promo_rule, policy, disclosure, automation in itertools.product(caps, (True, False), ("allowed", "none", "max:1"), (False, True), (True, False)):
        for promotional, links, (ai, line), automated, recent in itertools.product(
            (False, True), LINK_BODIES, ((False, False), (True, False), (True, True)), (True, False), (0, 1, 3)
        ):
            yield {"rules": {"frequency_cap": cap, "self_promo_allowed": promo_rule, "link_policy": policy, "disclosure_required": disclosure, "automation_allowed": automation},
                   "content": {"promotional": promotional, "links": links, "ai": ai, "disclosure_line": line}, "automated": automated, "recent_posts": recent}
    for missing in REQUIRED_RULE_KEYS:  # incomplete rules, one key missing at a time
        full = {"frequency_cap": None, "self_promo_allowed": True, "link_policy": "allowed", "disclosure_required": False, "automation_allowed": True}
        full.pop(missing)
        yield {"rules": full, "content": {"promotional": False, "links": 0, "ai": False, "disclosure_line": False}, "automated": True, "recent_posts": 0}


def run_case(case: dict):
    body, link = LINK_BODIES[case["content"]["links"]]
    if case["content"]["disclosure_line"]:
        body = f"{body}\n\n{DISCLOSURE}"
    rendition = Rendition(title="A test post", body=body, link_url=link, promotional=case["content"]["promotional"], ai_generated=case["content"]["ai"],
                          ai_disclosure=DISCLOSURE if case["content"]["ai"] and case["content"]["disclosure_line"] else None)
    recent = [NOW - 60 * (i + 1) for i in range(case["recent_posts"])]  # recent posts, all inside the window
    return check_rules(rendition, case["rules"], now=NOW, automated=case["automated"], post_times_since=lambda since: [t for t in recent if t > since])


def evaluate() -> dict:
    total = caught = missed = false_block = wrong_kind = allowed_ok = 0
    violating = 0
    by_kind: dict[str, dict[str, int]] = {}
    examples: list[dict] = []
    for case in cases():
        expected, kinds = oracle(case)
        decision = run_case(case)
        total += 1
        got_rules = {v.rule for v in decision.violations}
        if expected == "allow":
            if decision.action == "allow":
                allowed_ok += 1
            else:
                false_block += 1
                examples.append({"problem": "false block", "case": case, "got": decision.action, "rules": sorted(got_rules)})
            continue
        violating += 1
        for k in kinds:
            by_kind.setdefault(k, {"cases": 0, "caught": 0})["cases"] += 1
        if decision.action == expected and kinds <= got_rules:
            caught += 1
            for k in kinds:
                by_kind[k]["caught"] += 1
        elif decision.action == "allow":
            missed += 1
            examples.append({"problem": "MISSED violation", "case": case, "expected": expected, "kinds": sorted(kinds)})
        else:
            wrong_kind += 1  # stopped, but not for the right reason or in the right way
            examples.append({"problem": "stopped with the wrong action or reason", "case": case, "expected": expected, "kinds": sorted(kinds), "got": decision.action, "rules": sorted(got_rules)})
    return {"cases": total, "violating_cases": violating, "caught": caught, "missed": missed, "wrong_action_or_reason": wrong_kind, "allowed_correctly": allowed_ok,
            "false_blocks": false_block, "by_kind": by_kind, "problems": examples[:25]}


def main() -> None:
    r = evaluate()
    out = ROOT / "test-pack" / "rule-safety"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
    lines = ["# Rule safety", "",
             f"Test set: {r['cases']} cases (every combination of the five rules and the content that could break them; see `scripts/eval_rules.py`).",
             "The expected answer of each case comes from an independent oracle written from the brief's rules, not from the engine.", "",
             "| Result | Cases |", "|---|---|",
             f"| violations the engine **caught** (right action, right reason) | {r['caught']} of {r['violating_cases']} |",
             f"| violations **missed** (the post would have gone out) | {r['missed']} |",
             f"| stopped, but with the wrong action or reason | {r['wrong_action_or_reason']} |",
             f"| allowed posts that were correctly allowed | {r['allowed_correctly']} |",
             f"| **false blocks** (a legal post stopped) | {r['false_blocks']} |", "",
             "| Violation kind | Cases | Caught |", "|---|---|---|", *[f"| {k} | {v['cases']} | {v['caught']} |" for k, v in sorted(r["by_kind"].items())], "",
             "What this does not show: the set is mine, not IncuBrix's. The engine trusts the sender's `promotional` and `ai_generated` flags, plus a text check for obvious adverts"
             " (two or more advert phrases) where promotion is banned; a subtle advert that is neither flagged nor obvious would pass."]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:14]))


if __name__ == "__main__":
    main()
