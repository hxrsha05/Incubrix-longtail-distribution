# Advanced features against the running service

## 1. Tailoring: one post, fitted to a destination
- title: 192 characters -> 128 (Dev.to limit 128), ends: ...ctical ways to make your…
- tags: ['AI', 'Video Tools', 'indie', 'open source', 'extra'] -> ['ai', 'videotools', 'indie', 'opensource']
- canonical URL: https://example.com/launch
- change: tags cleaned to lower-case letters and digits, duplicates removed
- change: tags limited to 4 (dropped 1)
- change: canonical URL set to the original link
- change: title shortened at a word boundary: 192 -> 128 characters (limit 128)

## 2. Relevance score 1-5 with an explanation
- incubrix-mrss-feed-videos: 4/5: shared topics: video; same language; certified.
- local-lemmy-incubrix-test: 4/5: shared topics: video; same language; certified.
- hand-posted-forum-assisted: 2/5: no shared topics; same language; certified.
- local-ghost-blog: 2/5: no shared topics; same language; certified.
- mock-dev-to-account: 2/5: no shared topics; same language; certified.

## 3. AI rules capture: propose, then a person confirms
- proposal 3177d4c0cde4492c is PENDING (reader: mock-keyword-reader)
  - self_promo_allowed = False   quote: "Self-promotion is not allowed."
  - frequency_cap = {'max_posts': 1, 'per_seconds': 604800}   quote: "You may post one post per week."
  - link_policy = none   quote: "Do not post any links in the first comment or the body."
  - disclosure_required = True   quote: "AI-generated content must be disclosed with the [AI] tag."
  - automation_allowed = False   quote: "Bots are not allowed on this forum."
- rules BEFORE confirmation (unchanged): link_policy=max:1, frequency_cap={'max_posts': 1, 'per_seconds': 604800}
- confirmed by Demo Reviewer; rules AFTER: link_policy=none, automation_allowed=False, status note: confirmed_by:Demo Reviewer
- confirming the same proposal again: HTTP 409 IDEMPOTENCY_KEY_REUSED
