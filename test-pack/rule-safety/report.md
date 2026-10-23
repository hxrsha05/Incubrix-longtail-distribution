# Rule safety

Test set: 15557 cases (every combination of the five rules and the content that could break them; see `scripts/eval_rules.py`).
The expected answer of each case comes from an independent oracle written from the brief's rules, not from the engine.

| Result | Cases |
|---|---|
| violations the engine **caught** (right action, right reason) | 12317 of 12317 |
| violations **missed** (the post would have gone out) | 0 |
| stopped, but with the wrong action or reason | 0 |
| allowed posts that were correctly allowed | 3240 |
| **false blocks** (a legal post stopped) | 0 |

| Violation kind | Cases | Caught |
|---|---|---|
| ai_disclosure | 2592 | 2592 |
| automation | 3888 | 3888 |
| frequency_cap | 1620 | 1620 |
| link_policy | 5184 | 5184 |
| rules_missing | 5 | 5 |
| self_promotion | 3888 | 3888 |

What this does not show: the set is mine, not IncuBrix's. The engine trusts the sender's `promotional` and `ai_generated` flags, plus a text check for obvious adverts (two or more advert phrases) where promotion is banned; a subtle advert that is neither flagged nor obvious would pass.
