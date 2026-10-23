#!/bin/sh
# Start the fake platforms and the API inside one container, then load the demo catalogue.
set -e
PORT="${PORT:-8000}"

python -m uvicorn mock_platforms.main:app --host 127.0.0.1 --port 9100 --log-level warning &
python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &

# load the demo catalogue once the API answers (the seed script waits for /health)
INCUBRIX_TOKEN="${API_TOKEN:-}" python -m scripts.seed_catalogue "http://127.0.0.1:${PORT}" --profile demo || echo "catalogue seeding failed; the API is still up"
echo "demo ready on port ${PORT}"
wait
