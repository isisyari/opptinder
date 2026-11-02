# Deploying for free: Vercel + Neon + GitHub Actions

Nothing here asks for a credit card. Total cost is the domain, and even that is optional at first:
Vercel gives you `https://<project>.vercel.app` for free.

| Piece | Service | Free tier | Sign in with |
|-------|---------|-----------|--------------|
| Web app (FastAPI + the page) | Vercel | Hobby plan, non-commercial | GitHub |
| Database (Postgres) | Neon | 0.5 GB, auto-sleeps when idle and wakes on the next request | GitHub |
| Nightly scraping | GitHub Actions | 2,000 min/month; a run takes ~3 | already have it |
| Sign-in emails | Resend | ~100/day | GitHub or email |

## 1. Neon: create the database (5 minutes)

1. Go to https://neon.tech → Sign up with GitHub → **Create project**. Name it `f1-opportunities`,
   pick the region closest to your users (US East), leave the rest default.
2. On the project dashboard click **Connect** and copy the connection string. Make sure
   **Pooled connection** is on (the host contains `-pooler`); it looks like
   `postgresql://neondb_owner:npg_xxx@ep-xxx-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require`.
   That string is `DATABASE_URL` everywhere below. Treat it like a password.

## 2. Load the data from your laptop (once)

```bash
cd ~/Desktop/web
echo 'DATABASE_URL=postgresql://...paste it...' > .env       # .env is gitignored
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/migrate_sqlite_to_pg.py            # copies opportunities.db into Neon
.venv/bin/uvicorn app:app --reload                          # http://127.0.0.1:8000 now runs on Neon
```

The migrate script creates the tables and copies every opportunity, the 116 page-read eligibility
results, your swipes and your account. After this the local SQLite file is no longer used.

## 3. Vercel: deploy the app (5 minutes)

1. https://vercel.com → Sign up with GitHub → **Add New… → Project** → Import `isisyari/opptinder`.
2. Leave Framework Preset as **Other** and the root directory as is. Vercel finds `api/index.py` and
   `vercel.json` on its own.
3. Open **Environment Variables** and add:
   - `DATABASE_URL` = the Neon string
   - `ADMIN_TOKEN` = a long random string (run `openssl rand -base64 32` in your terminal)
4. **Deploy**. About a minute later you get `https://opptinder-xxx.vercel.app`. Open it, swipe,
   open `/admin` and paste the admin token.

Every `git push` to `main` redeploys automatically from now on.

## 4. GitHub Actions: nightly refresh (2 minutes)

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

- `DATABASE_URL` = the same Neon string
- `KAGGLE_API_TOKEN` = optional, from kaggle.com → Settings → API

Then **Actions → nightly ingest → Run workflow** once to confirm it goes green. It runs every day at
09:17 UTC after that and writes straight into Neon, so the site is always fresh.

## 5. Later: domain and email

- **Domain**: buy it (Porkbun or Cloudflare Registrar, ~$10/yr), then Vercel → project → **Settings →
  Domains → Add**. Vercel shows the two DNS records to add at the registrar and handles HTTPS itself.
- **Sign-in emails**: https://resend.com → API key. In Vercel add `RESEND_API_KEY`, and once you own
  the domain and verified it in Resend, `RESEND_FROM` = `F-1 Opportunities <hello@yourdomain>`. Until
  then sign-in codes print to the Vercel function logs (project → Logs), which is fine for testing.
- `PATHWAYS_OK=1` (Vercel and Actions) only after Pathways to Science says yes.

## Day to day

| Task | How |
|------|-----|
| Ship a change | `git push` |
| See server logs | Vercel → project → **Logs** |
| Run ingest now | GitHub → **Actions → nightly ingest → Run workflow** |
| Query the data | Neon → **SQL Editor** (`SELECT count(*) FROM users;`) |
| Back up | Neon keeps 24h of history on the free tier; for a file: `pg_dump "$DATABASE_URL" > backup.sql` |

## Things to know

- Neon's free compute sleeps after 5 idle minutes. The first visit after that takes a second or two
  longer while it wakes; nothing is lost.
- Vercel's rate limiter and the ingest-status endpoint are per function instance, so the "running"
  flag of `POST /api/admin/ingest` isn't reliable there. Use the GitHub workflow instead.
- The old VM route (`deploy/setup.sh`, Oracle or any Ubuntu box) still works if you ever want a
  long-running server: put `DATABASE_URL` in `/etc/f1.env` and everything else is unchanged.
