# Recertification and automatic demotion on the running service

## 1. First certification (a person starts it: uncertified destinations are never rechecked automatically)
- status: certified
- publishing while certified: HTTP 200 (live)

## 2. What is due right now
- due now: nothing for this destination (certified a moment ago)
- policy: re-check certified destinations after 7 days, demoted after 1 day, needs_attention after 1 hour

## 3. The platform changes its answer (a field is renamed), then a recertification run (force = do not wait a week)
- checked 10, demoted 2, transitions: [('recertification-demo-forum', 'certified', 'demoted')]
- status: demoted (the platform's response changed: field 'topic_id' is missing)
- publishing now: HTTP 422 DEST_SPEC_VIOLATION rule=destination_demoted
- suggested by the recommender: NO (left out)

## 4. The platform is fixed, the next recertification promotes the destination back
- transitions: [('recertification-demo-forum', 'demoted', 'certified')]
- status: certified
- publishing again: HTTP 200 (live)
