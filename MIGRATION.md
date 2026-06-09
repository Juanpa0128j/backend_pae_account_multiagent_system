# Migration Guide: Render → DigitalOcean App Platform

This guide walks through migrating the PAE backend web service from **Render** to **DigitalOcean App Platform**, while keeping **Supabase** for database, authentication, and pgvector RAG.

> **Scope**: This migration only moves the FastAPI compute layer. Supabase (Postgres + Auth + Storage) remains unchanged.

---

## Why DigitalOcean App Platform?

| Feature | Render (Free) | DigitalOcean (Basic) |
|---|---|---|
| **Cold starts** | 30–50s spin-down | None — always on |
| **Pre-deploy migrations** | Runs in `startCommand` (every cold start) | Native `PRE_DEPLOY` job |
| **Pricing** | Free (spins down) | **$5/mo** (always on) |
| **Buildpack `uv` support** | Manual `pip install uv` | Native |
| **Custom domains** | ✅ | ✅ |

---

## Prerequisites

- [DigitalOcean account](https://cloud.digitalocean.com/registrations/new)
- [`doctl` CLI](https://docs.digitalocean.com/reference/doctl/) installed and authenticated:
  ```bash
  doctl auth init
  ```
- GitHub repo connected to DigitalOcean (for auto-deploy on push)

---

## Step 1: Configure Environment Variables

In DigitalOcean App Platform dashboard → **Settings** → **Environment Variables**, add the same values you had in Render:

| Variable | Type | Value |
|---|---|---|
| `DATABASE_URL` | Secret | Your Supabase connection string |
| `SUPABASE_URL` | Secret | `https://<ref>.supabase.co` |
| `SUPABASE_JWT_SECRET` | Secret | From Supabase Dashboard → Settings → API |
| `GEMINI_API_KEY` | Secret | Your Google AI key |
| `HUGGINGFACE_API_KEY` | Secret | Your HuggingFace Inference API key |
| `LLAMA_CLOUD_API_KEY` | Secret | Your LlamaCloud key |
| `SECRET_KEY` | Secret | Strong random string (≥32 chars) |
| `ALLOWED_ORIGINS` | Secret | Your frontend URL(s), comma-separated |
| `APP_ENV` | General | `production` |
| `LOG_LEVEL` | General | `INFO` |
| `WORKFLOW_ENGINE` | General | `inline` or `inngest` |

> **Note**: `DATABASE_URL` still points to Supabase — nothing changes on the database side.

---

## Step 2: Deploy

### Option A: Dashboard (UI)

1. Go to [DigitalOcean Apps](https://cloud.digitalocean.com/apps)
2. Click **Create App**
3. Select your GitHub repo → branch `feat/migrate-to-digitalocean` (or `main` after merge)
4. DigitalOcean auto-detects Python + `uv` buildpack
5. Set environment variables (see Step 1)
6. Click **Launch**

### Option B: CLI (`doctl`)

```bash
# From repo root
doctl apps create --spec app.yaml
```

Or update an existing app:
```bash
doctl apps update --spec app.yaml <app-id>
```

---

## Step 3: Verify Migrations

DigitalOcean runs the `PRE_DEPLOY` job (`alembic upgrade head`) before each deployment.

Check the **Activity** tab → **Jobs** → `db-migrate` to confirm it ran successfully.

If you need to run migrations manually:
```bash
# Local, pointing at Supabase
DATABASE_URL=postgresql://... alembic upgrade head
```

---

## Step 4: Update CORS Origins

Your frontend needs to call the new DigitalOcean domain.

1. Find your app's domain: `https://pae-backend-xxxxx.ondigitalocean.app`
2. Update the `ALLOWED_ORIGINS` env var in DigitalOcean to include:
   - Your frontend production URL (e.g. `https://pae-frontend.vercel.app`)
   - The DO app domain (for health checks / direct API access)

Example:
```
ALLOWED_ORIGINS=https://pae-frontend.vercel.app,https://pae-backend-xxxxx.ondigitalocean.app
```

3. Trigger a redeploy for the CORS change to take effect.

---

## Step 5: Health Check

Test the deployed API:

```bash
curl https://pae-backend-xxxxx.ondigitalocean.app/health
```

Expected:
```json
{"status": "healthy", "database": "connected", "environment": "production"}
```

---

## Step 6: DNS / Custom Domain (Optional)

If you have a custom domain (e.g. `api.pae.app`):

1. In DO Dashboard → **Settings** → **Domains** → Add domain
2. Add the CNAME record they provide to your DNS provider
3. Update `ALLOWED_ORIGINS` to include the custom domain

---

## Rollback Plan

If something goes wrong:

1. Re-deploy on Render using the old `render.yaml` (it's in git history)
2. Or point your DNS back to the Render URL
3. No database rollback needed — Supabase was never touched

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'app'`
- Ensure `pyproject.toml` has `[tool.setuptools] packages = ["app"]`
- Ensure `main.py` is at repo root (it is)

### Alembic migration fails in PRE_DEPLOY job
- Check the job logs in DO Dashboard → Activity → Jobs → db-migrate
- Verify `DATABASE_URL` is correct and Supabase is accessible from DO's IP ranges
- Supabase allows connections from anywhere by default, but check your Supabase Dashboard → Database → Connection Pooling settings

### CORS errors from frontend
- Verify `ALLOWED_ORIGINS` includes your frontend URL exactly (including `https://`)
- Remember to redeploy after changing env vars

### Health check fails
- Check that `/health` returns 200 with `Content-Type: application/json`
- Verify `PORT` env var is not conflicting (DO sets `PORT=8080` automatically)

---

## What's Different from Render?

| Aspect | Render | DigitalOcean |
|---|---|---|
| **Migrations** | `startCommand: alembic upgrade head && uvicorn...` | `PRE_DEPLOY` job runs once before deployment |
| **Build** | `pip install uv && uv sync` | Native `uv` buildpack |
| **Domain** | `pae-backend.onrender.com` | `pae-backend-xxxxx.ondigitalocean.app` |
| **Sleep** | Free tier sleeps after 15 min idle | Never sleeps (Basic tier) |
| **Logs** | Render Dashboard | DO Dashboard → Runtime Logs |

---

## Post-Migration Checklist

- [ ] App deploys successfully on DO
- [ ] `db-migrate` PRE_DEPLOY job passes
- [ ] `/health` endpoint responds with `database: connected`
- [ ] Frontend can authenticate via Supabase JWT against DO backend
- [ ] Ingest pipeline works end-to-end
- [ ] Process pipeline works end-to-end
- [ ] Custom domain configured (if applicable)
- [ ] Old Render service shut down / deleted
