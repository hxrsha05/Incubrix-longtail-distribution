# Reliability

Measured by `python -m scripts.measure_reliability`; every call is a real HTTP request to the service, which calls the fake platforms over real HTTP.

## Publish success rate in simulation (A)

| Connector | Attempts | Succeeded |
|---|---|---|
| discourse | 40 | 40 |
| devto | 40 | 40 |
| hashnode | 40 | 40 |
| kit | 40 | 40 |
| webhook | 40 | 40 |
| **all** | 200 | **200 (100.0%)** |

## Duplicates under retry (B and C): must be 0

| Test | Cases | Posts intended | Posts on the platform | Duplicates |
|---|---|---|---|---|
| timeout after the platform created the post, retry with the same key (discourse) | 40 | 40 | 40 | **0** |
| timeout after the platform created the post, retry with the same key (devto) | 40 | 40 | 40 | **0** |
| the same request twice with the same key | 40 | 40 | 40 | **0** |

Total duplicates: **0**.

For the timeout tests, how the first call and the retry went:

- discourse: the first call timed out in 40 of 40 cases, the retry succeeded in 40 of 40
- devto: the first call timed out in 40 of 40 cases, the retry succeeded in 40 of 40

## Rate limits (D)

- 20 of 20 429 answers carried the reset time (15 s) to the caller; the retry with the same key succeeded 20 of 20 times; posts on the platform: 20

## Local instances: full certification cycles (publish, live, status, delete, gone)

| Destination | Cycles | Passed | Mean seconds | Slowest |
|---|---|---|---|---|
| local-lemmy-incubrix-test | 20 | 20 | 0.211 | 0.237 |
| local-ghost-blog | 20 | 20 | 0.08 | 0.181 |

Limits: simulated platforms behave as I modelled them; a real platform may fail in ways I did not think of. The local Lemmy and Ghost are real but tiny.
