# Test pack index

Every claim in the form points to a file here or in `evidence/`. Inputs are in `samples/`; outputs, including the failures, are below.
Regenerate any of them with the command in the last column (the service must be running: `docker compose up -d --build`).

| Claim | Input | Output (proof) | How it was made |
|---|---|---|---|
| A batch of posts goes out, one is blocked by the rules, one fails cleanly | `samples/inputs/*.json` | `test-pack/batch-run/` (one `*.result.json` per input and `summary.json`) | `docker compose exec api python -m app.cli run --input samples/inputs --output test-pack/batch-run` |
| Re-running the same folder never posts twice | the same inputs | `test-pack/batch-run-rerun/` (every published result has `"replayed": true`) | the same command, a second time |
| A timeout after the platform created the post does not cause a duplicate | made by the script | `test-pack/failure-demos/1-timeout-then-retry.json`, `transcript.md` | `python -m scripts.demo_failures` |
| A 429 with a reset time is reported and the retry works | made by the script | `test-pack/failure-demos/2-rate-limit.json` | `python -m scripts.demo_failures` |
| A renamed field fails certification, demotes the destination and leaves no stray post | made by the script | `test-pack/failure-demos/3-renamed-field.json` | `python -m scripts.demo_failures` |
| Missing credentials give an inconclusive run, not a demotion | made by the script | `test-pack/failure-demos/4-no-credentials.json` | `python -m scripts.demo_failures` |
| Each destination is certified and its test post deleted in time | the catalogue (`scripts/seed_catalogue.py`) | `evidence/certification/<destination>/run-*.txt` and `.json` | `POST /api/v1/jobs` with `{"kind":"certify_batch"}` |
| CSV import of 1,000 rows with duplicates, bad URLs, dead sites, mixed languages | `samples/catalogue/sample-1000.csv` | `test-pack/csv-import/sample-1000/` (`report.csv`, `report.json`, `summary.txt`) | `python -m scripts.import_demo` |
| Tailoring, relevance score, AI rules capture with human confirmation | made by the script | `test-pack/advanced-demos/` (`transcript.md` and one JSON per feature) | `python -m scripts.demo_advanced` |
| Recommender precision@10 on a labelled set | `samples/recommender/labelled_set.json` (synthetic, read its description) | `test-pack/recommender/eval.md`, `eval.json` | `python -m scripts.eval_recommender` |
| Failed recertification demotes; demoted destinations are refused and not recommended; a later pass promotes back | made by the script | `test-pack/recertification/transcript.md` | `python -m scripts.demo_recertification` |
| No secrets in the repository or the service logs | the repository | `evidence/secret-scan.txt` | `python -m scripts.secret_scan --docker-logs` |
| The whole test suite passes with no keys | `tests/` | screenshot `evidence/manual-demos/tests/01-pytest-208-passed.png` (the suite has grown since: run `python -m pytest`) | `python -m pytest` |

Screenshots (taken by hand) are in `evidence/manual-demos/`, each described in that folder's `README.md`.
