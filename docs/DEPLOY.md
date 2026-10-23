# Putting the live demo online

IncuBrix wants: **a working URL, an access token if protected, the cold-start time, and a rate limit** (brief, "Live demo URL"). The link must
keep working **until results are announced**.

The demo is `Dockerfile.demo`: one small container with the API and the fake platforms, in simulation mode (no real accounts). It has everything
except Lemmy and Ghost, which need their own servers (those are proven with `docker compose` and the evidence in `evidence/`).

What the demo already has switched on: an access token (`API_TOKEN`), a rate limit (`RATE_LIMIT_PER_MINUTE=120` per client), no private
addresses reachable except its own fake sites, a non-root user, and the failure switches (`/api/v1/simulation/*`) behind the same token.

## Option A: Render (free, no card needed for a free web service; it sleeps when idle)

1. Make sure the latest code is pushed to your private GitHub repository.
2. Go to https://render.com and sign in with GitHub. Choose **New -> Blueprint**, pick this repository. Render reads `render.yaml` and creates the
   service `incubrix-demo`.
3. When it asks for `SERVICE_BASE_URL`, type the address Render shows for the service (for example `https://incubrix-demo.onrender.com`). If you do not
   know it yet, leave it, finish the deploy, then add it under **Environment** and redeploy.
4. Open the service -> **Environment** -> copy the generated **API_TOKEN**. That is the token for the form.
5. Test (replace the address and token):
   ```
   curl https://incubrix-demo.onrender.com/health
   curl -H "Authorization: Bearer YOUR_TOKEN" https://incubrix-demo.onrender.com/api/v1/destinations
   ```
6. **Cold start:** a free Render service sleeps after about 15 minutes without traffic and needs roughly 30 to 60 seconds to wake. Measure it: wait
   20 minutes, then time `curl -s -o /dev/null -w "%{time_total}\n" https://.../health`. Write that number in the form. To avoid it during the review
   you may point a free uptime pinger (for example UptimeRobot) at `/health` every 5 minutes; if you do, say so in the form.
7. Keep the service running until results are announced. Do not delete it.

## Option B: your own laptop through a tunnel (only if Render is not possible)

The laptop must stay on and connected for the whole review period, so this is the weaker choice.
1. `docker compose up -d --build` and `python -m scripts.seed_catalogue` as in the README. Set `API_TOKEN` and `RATE_LIMIT_PER_MINUTE=120` in `.env`, then
   `docker compose up -d` again.
2. Install `cloudflared` and run `cloudflared tunnel --url http://localhost:8000`. It prints a public `https://....trycloudflare.com` address.
3. Put that address in `SERVICE_BASE_URL` in `.env` and restart the `api` container. Use the address and token in the form. The address changes every
   time the tunnel restarts, which is another reason to prefer Option A.

## What to put in the form

| Field | Value |
|---|---|
| Live demo URL | the address from Render (or the tunnel) |
| Access token | the `API_TOKEN` value (type it into the form only; never commit it) |
| Cold-start time | the number you measured |
| Rate limit | 120 requests per minute per client (429 `RATE_LIMITED` with `Retry-After`) |
| How to try it | `/docs` (interactive API page), and the README section "How to trigger each hard case on a running demo" |

## Check before you submit

- `GET /health` answers 200 without a token; every other call without a token answers 401 `UNAUTHORIZED`.
- `POST /api/v1/publish` to `mock-discourse-forum` with a fresh `Idempotency-Key` works; the same call again returns `Idempotent-Replayed: true`.
- `POST /api/v1/simulation/fail` then publish shows the failure; `POST /api/v1/simulation/reset` clears it.
- `GET /api/v1/catalogue/export?format=csv` returns the destination records.
