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
