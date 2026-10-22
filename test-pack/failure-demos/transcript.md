# Failure demos against the running service

## 1. Timeout after the platform created the post, then a retry with the same key
- first call: HTTP 503 PLATFORM_UNAVAILABLE (outcome_unknown=True) after 10.0 s
- the platform DID create the post: discourse_topics = 1
- retry with the same Idempotency-Key: HTTP 200, topic 101
- topics on the platform after the retry: 1  (duplicates: 0)

## 2. Rate limit with a reset time
- HTTP 429 PLATFORM_RATE_LIMIT retryable=True retry_after_s=15.0
- retry with the same key after the limit cleared: HTTP 200 (live)

## 3. The platform renames a field: certification fails and the destination is demoted
- certification verdict: failed; reason: the platform's response changed: field 'topic_id' is missing
- destination status now: demoted
- test posts left behind on the platform: 0  (the stray post was found and deleted)

## 4. Expired credentials
- verdict: inconclusive (CONNECTION_AUTH_EXPIRED); status: needs_attention
