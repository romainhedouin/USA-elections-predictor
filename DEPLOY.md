# Deploying the USA Election Live Predictor to Railway

One always-on service that serves `map.html` and refreshes the CSVs from inside
the same process. Verified against docs.railway.com in September 2026; every
version-sensitive claim cites its doc page. Anything I could not confirm is
marked **[UNCONFIRMED]**.

---

## 0. Why this shape

| Decision | Reason |
| --- | --- |
| One service, not web + cron | A volume attaches to one service, and Railway's cron minimum interval is 5 minutes — too slow for election night. The scheduler therefore lives in the web process. |
| Volume for the CSVs | Files outside a volume do not survive a deploy, and volumes are not mounted during build, so the data has to be written at runtime onto the volume. <https://docs.railway.com/volumes> |
| One replica | "Replicas cannot be used with volumes." A second replica would also mean a second scheduler racing on the same files. <https://docs.railway.com/volumes> |
| Serverless/sleep off | The first request to a slept service "may return a 502 Bad Gateway before the service is ready". <https://docs.railway.com/reference/app-sleeping> |

---

## 1. Prepare the repo (before touching Railway)

Commit these to the root of `romainhedouin/USA-elections-predictor`:

```
server.py          # from this scratch dir
railway.toml       # from this scratch dir
requirements.txt   # already split; see 1a
map.html           # already there
fetch_results.py   # already there (or in progress)
generate_mock_data.py, races.py   # already there
```

### 1a. requirements.txt split — already done

Railpack pip-installs the **root** `requirements.txt`, so that file is what the
deployment gets. The root file already carries only what `server.py` and
`fetch_results.py` need:

```
requests==2.32.3
```

`selenium` and `beautifulsoup4` live in `legacy/requirements.txt` instead
(`pip install -r legacy/requirements.txt` if you need to run the superseded
scraper), because a Railpack container has no Chrome binary and a Selenium
install there could never run. This split landed in commit `b083b02`; nothing
further to do here.

### 1b. Python version — already pinned

`.python-version` at the repo root already pins Python to `3.13` (added in
the same commit, `b083b02`), so a future Railpack default bump can't surprise
a redeploy. Precedence, for reference: `RAILPACK_PYTHON_VERSION` env var →
`.python-version` / `.tool-versions` / `mise.toml` → `runtime.txt`.
(<https://railpack.com/languages/python/>)

### 1c. `--out-dir` — already supported

Both `fetch_results.py` and `generate_mock_data.py` already accept `--out-dir`
(added in commit `2ea3110`, the same commit that introduced `server.py`), so
`server.py`'s calls into either CLI write straight onto the mounted volume.
No patch needed here.

### 1d. No Procfile — deliberately

I did not write one, because it would be dead weight at best and a trap at
worst:

- `railway.toml` sets `deploy.startCommand = "python server.py"`, and
  "Configuration defined in code will always override values from the
  dashboard" (<https://docs.railway.com/config-as-code/reference>). The start
  command is already unambiguous.
- Railpack honours a Procfile but explicitly deprecates it: "Procfiles are
  deprecated and natively setting the start command with `RAILPACK_START_CMD`
  or in the `railpack.json` config file is recommended."
  (<https://railpack.com/config/procfile/>)
- Two files declaring the start command is a future bug — you change one, the
  other silently wins.

If you ever want to override the start command without touching
`railway.toml`, set the `RAILPACK_START_CMD` variable instead.

---

## 2. Create the project from GitHub

**Dashboard**

1. <https://railway.com/new>, or **New Project** from the dashboard.
2. Choose **Deploy from GitHub repo**.
3. If this is your first project, authorise the Railway GitHub App and grant it
   access to `USA-elections-predictor` (**Configure GitHub App** if the repo
   does not appear).
4. Pick `romainhedouin/USA-elections-predictor`.
5. Railway creates the service and starts a first build immediately. **Let this
   first deploy fail or misbehave** — the volume and variables are not set up
   yet. Do not debug it until step 5.

Services linked to a GitHub repo auto-deploy on every push to the connected
branch. <https://docs.railway.com/deployments/github-autodeploys>

**CLI equivalent** — note the difference: `railway up` uploads your local
directory as a one-shot deploy and does **not** set up deploy-on-push. Only the
GitHub-linked route gives you auto-deploy. Use the dashboard for step 2, then
link your local checkout for the CLI commands below:

```bash
brew install railway          # or: npm i -g @railway/cli
railway login
cd /Users/romainhedouin/Documents/personal/usa-election
railway link                  # pick the workspace/project/service interactively
```

---

## 3. Add the volume and mount it at `/data`

**Dashboard**

1. Open the project canvas.
2. Press **⌘K** for the command palette and choose the volume option, **or**
   right-click on empty canvas and pick the volume entry from the menu. (Those
   two are the documented paths; <https://docs.railway.com/volumes>)
3. When prompted, **select the service** you just created.
4. Set the **mount path** to exactly:

   ```
   /data
   ```

   Absolute, and deliberately *not* inside the app directory — a volume mounted
   over your source would hide it.
5. Size: the Hobby default is **5GB** (Pro 50GB, self-serve up to 1TB). Three
   CSVs are ~12KB total, so the default is already absurdly generous — accept it
   and do not raise it. **[UNCONFIRMED]** whether the $0.15/GB/month is charged
   on provisioned or on used bytes; the pricing page says only "You are only
   charged for the resources you actually use", which implies used. Either way
   the worst case here is $0.75/month for the 5GB default, so it is not worth
   optimising.

**CLI equivalent**

```bash
railway volume add --mount-path /data     # mount path must start with "/"
railway volume list
railway volume browse /                   # TUI file browser over the volume
```

Things to keep in mind (all from <https://docs.railway.com/volumes>):

- "Volumes are mounted to your service's container when it is started, not
  during build time." Anything written at build time is lost — which is why
  seeding happens in `server.py` at boot, not in a build step.
- Data survives restarts and redeploys.
- "there will be a small amount of downtime when re-deploying a service that has
  a volume attached", **even with a healthcheck configured**, because two
  deployments cannot mount the same volume at once. Expect a few seconds of
  downtime per deploy. Do not deploy at 9pm on election night.
- Railway injects `RAILWAY_VOLUME_MOUNT_PATH` and `RAILWAY_VOLUME_NAME`. You
  could set `DATA_DIR` from the former, but an explicit `DATA_DIR=/data` is
  easier to reason about.

---

## 4. Environment variables

**Dashboard:** service → **Variables** tab → **New Variable** (or **Raw Editor**
to paste all of them at once).

| Variable | Value | Why |
| --- | --- | --- |
| `DATA_DIR` | `/data` | Must equal the volume mount path. |
| `REFRESH_SECONDS` | `900` | 15 min normally. Drop to `60` on election night. Floor is 30s. |
| `NBC_CYCLE` | `2024` | `races.py` says 2026/2028 NBC paths 404 until results exist. Bump on election night. |
| `RACES` | `president,senate,governor` | Or omit — the default is all four (add `house` here too if you want it refreshed). |
| `FETCH_TIMEOUT` | `300` | Hard kill per race. ~10s expected, so 300 is very generous. |

**Do not set `PORT`.** Railway injects it, and `server.py` reads it. The
healthcheck also uses `PORT` to know where to probe.
<https://docs.railway.com/guides/healthchecks>

**CLI equivalent** (`railway variable set`; the old `railway variables --set`
form is deprecated — <https://docs.railway.com/cli/variable>):

```bash
railway variable set DATA_DIR=/data --skip-deploys
railway variable set REFRESH_SECONDS=900 --skip-deploys
railway variable set NBC_CYCLE=2024 --skip-deploys
railway variable set RACES=president,senate,governor --skip-deploys
railway variable set FETCH_TIMEOUT=300          # last one, triggers one redeploy
railway variable list
```

---

## 5. Deploy and generate the public domain

1. Service → **Deployments** → **Deploy** (or push a commit).
2. Watch the build log: Railpack should detect Python, run
   `pip install -r requirements.txt`, and start with `python server.py` from
   `railway.toml`.
3. Service → **Settings** → **Networking** → **Public Networking** →
   **Generate Domain**. You get `something.up.railway.app`.
   <https://docs.railway.com/guides/public-networking>
4. If you see **"Application failed to respond"** (502), the app is not bound to
   the injected `PORT` — but `server.py` binds `0.0.0.0:$PORT`, so the real
   cause is more likely a crash at boot. Check the deploy logs.

**CLI equivalent**

```bash
railway domain          # generates a *.up.railway.app domain
railway logs            # runtime logs
railway logs --build    # build logs
```

`railway.toml` already sets `healthcheckPath = "/healthz"`, so Railway waits for
a 2xx there before switching traffic to the new deploy. Notes from
<https://docs.railway.com/guides/healthchecks>:

- Any 2xx counts as healthy; the probe comes from hostname
  `healthcheck.railway.app` and hits your `PORT`.
- Default timeout is 300s; `railway.toml` tightens it to 120s.
- **The endpoint is only checked at deploy time, not continuously.** `/healthz`
  is your monitoring surface, not Railway's.
- `/healthz` returns 200 whenever every configured race has a CSV on the volume
  — *including stale ones* — and 503 only when there is genuinely nothing to
  serve. A deploy that cannot even seed mock data will therefore fail and leave
  the previous deploy running, which is what you want.

---

## 6. Attach a custom domain

<https://docs.railway.com/networking/domains/working-with-domains>

1. Service → **Settings** → **Networking** → **Custom Domain**, enter e.g.
   `map.example.com`.
2. Railway shows **two** DNS records. **Both are required — the domain will not
   verify with only the CNAME:**
   - a **CNAME** pointing at a Railway target of the form `xxxxxx.up.railway.app`
   - a **TXT** record for ownership verification
3. Create both at your DNS provider. Wait for verification.
4. TLS: "Certificate issuance should happen within an hour of your DNS being
   updated." Certificates are valid 90 days and auto-renew at 30 days remaining.

**Apex/root domains** (`example.com` with no subdomain): plain CNAME at the apex
is not legal DNS. Railway documents provider workarounds — Cloudflare CNAME
flattening, DNSimple ALIAS, bunny.net ANAME, Namecheap root CNAME — and lists
Route 53, Azure DNS and GoDaddy as not supported. **[UNCONFIRMED]** that
provider list is single-sourced; check your provider before committing. Easiest
answer: use a subdomain like `map.` and redirect the apex.

**Cloudflare:** with the orange cloud (proxy) **on**, set SSL/TLS mode to
**Full**. There is a documented edge case where nested subdomains need the grey
cloud (DNS-only) or Cloudflare Advanced Certificate Manager — **[UNCONFIRMED]**,
re-check for your exact domain shape.

**Limit:** Hobby allows **2 custom domains per service** (Pro 20).
**[UNCONFIRMED]** — single-sourced.

**CLI** (<https://docs.railway.com/cli/domain>)

```bash
railway domain map.example.com
railway domain list
railway domain status map.example.com
railway domain certificate retry
```

---

## 7. Confirm it works

```bash
BASE=https://<your-domain>

# 1. page loads
curl -sI $BASE/ | head -3                       # 200, text/html

# 2. CSV loads with the right type and no caching
curl -sI $BASE/raw_data.csv | grep -i 'content-type\|cache-control'
# expect: Content-Type: text/csv; charset=utf-8
#         Cache-Control: no-cache

# 3. health
curl -s $BASE/healthz | python3 -m json.tool
```

In `/healthz` check:

- `"status": "ok"` (`degraded` = serving fine but the last refresh failed;
  `unhealthy` = no CSVs at all)
- `"data_dir": "/data"` — **if this says anything else, the volume is not
  mounted and your data is on ephemeral disk that dies with the next deploy**
- each race's `last_success` is recent and `csv_age_seconds` < `REFRESH_SECONDS`
  plus a fetch's worth of slack
- `last_error: null`

Then in the **Deployments → Logs** viewer, look for one line per race per cycle:

```
INFO  refresh race=president status=ok rows=3143 bytes=412119 duration=11.4s source=fetch
INFO  cycle done ok=3/3 duration=31.8s next_in=900s
```

and on the first-ever boot with an empty volume:

```
INFO  boot seed starting for president, senate, governor (no CSVs found in /data)
INFO  refresh race=president status=ok rows=70 bytes=4757 duration=0.1s source=mock
```

A failed refresh logs `status=failed ... kept=True`, meaning the previous CSV is
still being served. That is the designed behaviour, not an outage.

**Volume persistence check:** hit `/healthz`, note `csv_bytes`, redeploy, hit it
again. Same bytes and a `boot seed skipped` log line = the volume is working.

**Do not enable Serverless** (Settings → **Deploy** → Serverless). It would let
the service sleep, and the first request to a slept service can 502. The
scheduler's outbound traffic prevents sleeping anyway, so it would only cost you
reliability. <https://docs.railway.com/reference/app-sleeping>

### Optional hardening — `requiredMountPath`

`railway.schema.json` has a `deploy.requiredMountPath` string, described as
"Required mount path for the deployment", which looks like it fails the deploy
if the volume is not mounted there — exactly the silent failure you want to
catch. **[UNCONFIRMED]**: it is in the schema but I found no prose docs for it,
so I left it out of `railway.toml`. Add `requiredMountPath = "/data"` under
`[deploy]` if you want to try it, and be ready to remove it.

---

## 8. Election-night runbook

```bash
railway variable set NBC_CYCLE=2028 --skip-deploys
railway variable set REFRESH_SECONDS=60
```

The second command triggers a redeploy, which means a few seconds of downtime
(volume remount). Do it before polls close, not during. Then watch
`/healthz`: if `consecutive_failures` climbs while `csv_age_seconds` grows, NBC's
markup or URL changed — the map keeps serving the last good data while you fix
`fetch_results.py`.

---

## 9. Cost and usage limits

### Rates (<https://docs.railway.com/reference/pricing>)

| Resource | Rate |
| --- | --- |
| RAM | $10 / GB / month |
| vCPU | $20 / vCPU / month |
| Volume storage | $0.15 / GB / month |
| Egress | $0.05 / GB |

Hobby is **$5/month, which includes $5 of usage credit** — you pay
`max($5, actual usage)`, and credit does not roll over.

### This service, realistically

| Line item | Estimate |
| --- | --- |
| RAM, 128MB always on | 0.125 GB × $10 = **$1.25/mo** |
| vCPU — idle except ~30s of I/O-bound work every 15 min | ~0.01–0.03 vCPU avg ≈ **$0.20–$0.60/mo** |
| Volume | **$0.00–$0.75/mo** — $0.15 × 1GB if billed on provisioned size, effectively $0 if billed on the ~12KB actually used, $0.75 worst case at the 5GB Hobby default |
| Egress — 44KB page + ~12KB CSVs ≈ 56KB/visit (d3 and the county topology come from jsDelivr, not Railway) | 10k visits ≈ 0.56GB ≈ **$0.03/mo** |
| **Total usage** | **≈ $1.50–$2.70/mo** |
| **What you actually pay** | **$5.00/mo** — usage sits well under the included credit |

So the honest answer: **$5/month, and the app is free inside that.** You would
need roughly 2× this footprint before the bill moves at all.

The variable worth watching is **RAM**, because it is billed continuously. 128MB
is a reasonable target (CPython baseline plus a short-lived fetch subprocess),
but confirm it on the service's **Metrics** tab after a day. At 512MB steady
state you would be paying $5/mo in RAM alone and would start exceeding the
credit. Election-night traffic mostly shows up as egress: even 100k pageviews is
~5.6GB ≈ $0.28.

### Usage limits (<https://docs.railway.com/reference/usage-limits>)

Workspace **Usage** page (account switcher, top-left, to pick the workspace) →
**Set Usage Limits**. Two independent fields:

- **Custom email alert** — soft. "we will email you that this threshold has been
  met. Your resources will remain unaffected."
- **Hard limit** — "all your workloads will be taken offline". Warning emails at
  75%, 90%, 100%. It stops services; **it does not delete data or volumes.**
  Railway auto-redeploys once you raise or remove the limit.

Recommended:

| Setting | Value | Why |
| --- | --- | --- |
| Email alert | **$8** | ~4× expected usage. If this fires, something is wrong — a crash-loop, a runaway fetch, or unexpected traffic. |
| Hard limit | **$10** | The documented minimum; anything lower is rejected. Your ~$2 of usage will never approach it. |

**Before election night, raise the hard limit to ~$25 or remove it.** A hard
limit takes the site offline the moment it trips, which is the one night you
cannot afford that.

---

## Summary of unconfirmed items

1. The apex-domain provider support list (single-sourced).
2. The Cloudflare nested-subdomain / Advanced Certificate Manager edge case.
3. Custom domains per plan (2 Hobby / 20 Pro) — single-sourced.
4. `deploy.sleepApplication` and `deploy.requiredMountPath` — both validate
   against the live `railway.schema.json` but have no prose documentation.
5. The exact volume-creation button label. Only ⌘K and right-click-canvas are
   documented; the current UI may also have a "+ Create" button.
6. Full start-command precedence between the dashboard, `railway.toml`, a
   Procfile, and `RAILPACK_START_CMD`. `railway.toml` overriding the dashboard
   *is* documented; where a Procfile sits relative to it is inference.
7. Whether volume storage bills on provisioned or used bytes.
8. The exact date Railpack replaced Nixpacks as the default builder (secondary
   sources disagree). That it *is* the current default is confirmed.
