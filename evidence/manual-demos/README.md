# Manual demonstrations

These are **manual demonstrations**, not the official certification evidence. The official, automatically generated
run records for each destination will be in `evidence/certification/` once the certification harness exists.

## Ghost, 22 Oct 2026

Run by a script that called our own API (`POST /connector/v1/ghost/publish`, then `get_status`, `delete`, `get_status`)
against the local Ghost instance started by `docker compose`. The screenshots were then taken by hand in a browser.

| File | What it shows |
|---|---|
| `ghost/01-published-live-1233.png` | The published post on the local Ghost blog. The tab had been loaded before the deletion, so it still shows the page although the post was deleted at 12:33:01. |
| `ghost/02-deleted-404-1234.png` | The same URL after reloading: Ghost answers "404, Page not found". |

API log of the cleanup (local time):

```
12:33:01  get_status -> live
12:33:01  delete     -> deleted: true
12:33:01  get_status -> not_found
12:33:01  public page -> HTTP 404
```

The same request sent twice with the same idempotency key produced one post: the second answer was identical and carried
the header `Idempotent-Replayed: true`.

## Lemmy: post made by the batch runner

| File | What it shows |
|---|---|
| `lemmy/01-batch-runner-post-live.png` | The post "Batch runner test post" in the community `incubrix_test` on the local Lemmy (http://localhost:1234), published by the service's account through `python -m app.cli run --input samples/inputs --output test-pack/batch-run`. The matching record is `test-pack/batch-run/03-lemmy-ok.result.json` (it holds the Lemmy post id and the live URL). |

## Certification evidence opened from the S3 download link

| File | What it shows |
|---|---|
| `certification/01-s3-download-link-ghost-run.png` | The certification summary of the Ghost destination (run 822e6775e2ca, verdict PASSED, every step ok, test post deleted 0.052 s after it was created, limit 600 s), opened in the browser from the presigned S3 link (`localhost:9000/incubrix-distribution/certification/local-ghost-blog/...`). The same record is in `evidence/certification/local-ghost-blog/`. |

## The test suite

| File | What it shows |
|---|---|
| `tests/01-pytest-208-passed.png` | `python -m pytest` run from the project folder in VS Code: 208 passed, 18 skipped in 11.45 s (the 18 skipped are the live Lemmy and Ghost tests, which switch on only when those servers are running and their addresses are set). 23 Oct 2026, 02:04 PM. |

## The hard cases, run against the live service

| File | What it shows |
|---|---|
| `failure-demos/01-demo-failures-output.png` | Output of `python -m scripts.demo_failures` (23 Oct 2026, 02:05 PM): (1) the platform timed out after creating the post, the retry with the same Idempotency-Key returned the same topic, 1 topic on the platform, 0 duplicates; (2) a 429 with a 15 s reset time, then success on retry; (3) a renamed field made certification fail, the destination was demoted, and 0 test posts were left behind; (4) missing credentials gave an inconclusive run and status needs_attention. The same cases are saved as JSON in `test-pack/failure-demos/`. |

## The public demo

| File | What it shows |
|---|---|
| `live-demo/01-live-demo-checks-all-pass.png` | `python -m scripts.check_live_demo https://incubrix-demo.onrender.com` run from a developer laptop against the public demo on Render (23 Oct 2026, evening): health answered in 0.81 s, a call without the token was refused with 401, the catalogue listed 7 destinations, a publish went live, the same Idempotency-Key returned the same post, a late platform answer gave outcome unknown (503), the retry succeeded and exactly 1 post existed afterwards (0 duplicates), and the destination export worked. All 10 checks passed. The token is not shown. |
