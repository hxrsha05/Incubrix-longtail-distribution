# Real catalogue (catalogue only: nothing here is ever posted to)

- researched candidates: 168; after removing duplicates: 152 (16 duplicates or unusable addresses dropped)
- import check against the real internet: **152 accepted**, 0 unreachable, 0 invalid, 0 duplicates; 30.93 s
- of the accepted: 137 answered HTTP 200 to the importer; 10 blocked the importer (HTTP 403) and 5 were not visited because robots.txt forbids it; those are kept with a warning, because the research step (a person-like browser read) found them live
- destination records written: **152** (`real_catalogue.csv`, the fields the brief requires)
- rules proposed from the community's own quoted words for 9 destinations; the others keep the conservative default
- by category: {'blog': 48, 'community': 21, 'directory': 6, 'forum': 69, 'newsletter': 3, 'video showcase': 5}
- by connector type: {'assisted': 62, 'devto': 12, 'discourse': 42, 'ghost': 1, 'hashnode': 8, 'lemmy': 27}
- relevance score to IncuBrix's audience (1-5): 1: 0, 2: 24, 3: 75, 4: 38, 5: 15
- with a member count: 74; with a rules page address: 152

## What is and is not verified

- Every address answered a real request from `scripts/build_real_catalogue.py` (the import report has the status, response time and final address), and robots.txt was respected.
- Quotes come from each community's own rules page (copied by the research step and checked there as exact substrings); the five rules are then read from those quotes by software.
- Rules are **unconfirmed**: a person must confirm them before any would be used. `geo` was not researched. Member counts are the ones the sites state themselves.
- Nothing was posted to any of these communities, and none can be (they are flagged catalogue-only; certification and publishing refuse them).
