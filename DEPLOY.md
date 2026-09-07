# Deploying the standalone Account Research app to Render

This app is **independent** of the Skeqi-EmailDrafter CRM. The CRM embeds it in
an iframe and shares nothing else: no database, API, session, report or key.

---

## 1. Create the Render service

Repository: this folder. Render reads `render.yaml`.

| Setting | Value |
|---|---|
| Environment | Python |
| Build | `pip install -r requirements.txt` |
| Start | `gunicorn --workers 1 --threads 8 --timeout 600 --bind 0.0.0.0:$PORT app:app` |
| Health check | `/healthz` |

**One worker only.** Job and batch state lives in memory and is owned by
background threads; a second worker would answer polls for jobs it never ran.
Concurrency comes from `--threads`, not from workers.

## 2. Set the environment variables

Secrets are set in the Render dashboard (`sync: false`), never committed.
`ai_credentials.env` is git-ignored and is not deployed — on Render the same
keys are read from the environment instead.

**Access gate — set both, or the app is public:**

```
APP_ACCESS_USERNAME
APP_ACCESS_PASSWORD
APP_ACCESS_SECRET          (optional)
```

**Embedding — the CRM origin, scheme + host, no path, never `*`:**

> **The live CRM is `skeqi-emaildrafter-i46d.onrender.com`.** The older
> `skeqi-emaildrafter.onrender.com` is obsolete and now 404s at Render's edge, so
> a probe against it looks like a failed deploy when nothing is wrong. Confirmed
> against production 2026-09-06. The engine's own hostname is still written as
> `<this-service>` below because it has not been recorded; fill it in the next
> time you are in the Render dashboard.


```
ALLOWED_FRAME_ANCESTORS=https://skeqi-emaildrafter-i46d.onrender.com
```

**DashScope + Apollo:** see `render.yaml` and `ai_credentials.env.example`.

## 3. Point the CRM at it

In the **CRM** service:

```
CURRENT_ACCOUNT_RESEARCH_URL=https://<this-service>.onrender.com
```

## 4. Verify

```
curl -sI https://<this-service>.onrender.com/healthz
curl -sI https://<this-service>.onrender.com/ | grep -i content-security-policy
# expect: frame-ancestors 'self' https://skeqi-emaildrafter-i46d.onrender.com
curl -so /dev/null -w '%{http_code}\n' https://<this-service>.onrender.com/api/reports
# expect: 401
```

Then open the CRM → Account Research → Current. Sign in once inside the frame.

---

## ⚠ Persistence: saved data does NOT survive a restart or redeploy

Render's filesystem is **ephemeral** unless a paid persistent Disk is attached.
Everything this app saves is written to local disk:

| Data | Path | Survives restart / redeploy? |
|---|---|---|
| Research JSON | `reports/<Company>/research*.json` | **No** |
| Company PDFs | `reports/<Company>/*.pdf` | **No** |
| Compiled portfolio PDFs | `reports/*.pdf` | **No** |
| Batch state | `reports/_batches/<id>.json` | **No** |
| Run history | `test_results/*.json` | **No** |
| Evidence cache | `evidence_cache/*.json` | **No** |

On the free plan the service also **spins down after inactivity**, and the next
request starts a fresh container — so data can disappear without any deploy.

**What this means for the live test**

- Treat every report as disposable. Download the PDF or the ZIP if you want to
  keep it.
- A batch interrupted by a spin-down loses its resume state and restarts.
- The evidence cache is lost, so re-running a company costs tokens again.
- Nothing is corrupted; the app recreates every directory on demand.

**Not fixed here, deliberately.** Durable storage means attaching a Render Disk
(paid, single instance) or moving reports to object storage. Both are real
changes to how the app stores data and were out of scope for this integration.
