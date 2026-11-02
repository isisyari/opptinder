# F-1 Opportunities — "Tinder for opportunities" international students can actually take

A swipe feed of hackathons, conferences, fellowships, competitions, research, scholarships,
on-campus jobs and accelerators, filtered for what an F-1 student can do **without CPT/OPT**.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python ingest.py          # pulls Devpost + Devfolio + seed/manual.csv into opportunities.db
.venv/bin/uvicorn app:app --reload  # then open http://127.0.0.1:8000
```

Keyboard: `→` save, `←` pass. Drag cards on touch. "Saved" tab lists what you kept, sorted by deadline.

## Layout

```
models.py            Opportunity dataclass, f1_flag() rule, SQLite upsert
schema.sql           tables: opportunities (eligibility is first-class), swipes
ingest.py            runs every source, marks past-deadline rows expired
sources/devpost.py   Devpost's own JSON endpoint (undocumented; needs browser UA)
sources/devfolio.py  Devfolio public search endpoint
sources/hackerearth.py HackerEarth's JSON events feed (small, all online, prize hackathons)
sources/unstop.py    Unstop public API: online hackathons + competitions WITH a cash prize (India-heavy otherwise)
sources/hackclub.py  Hack Club Hackathons API (documented; mostly high-school events, tagged; attribution required)
sources/kaggle.py    Kaggle competitions via the official API (needs a free token, see below)
sources/zindi.py     Zindi data-science competitions (JSON; restricted-entry ones flagged)
sources/badgeup.py   BadgeUp README tables (github.com/Jose-Gael-Cruz-Lopez/BadgeUp) -> ~55 undergrad conferences
sources/mentorship.py "Academic Research Opportunities for International Students" GitHub README -> ~30 research programs
sources/pathways.py  Pathways to Science: 134 funded research programs open to non-US citizens. OPT-IN (PATHWAYS_OK=1)
sources/aggregators.py Opportunity Desk, OYA Opportunities, Opportunities Corners (WordPress JSON) -> fellowships,
                     scholarships, competitions, conferences. ~300 rows; heuristic filters, see "Aggregator blogs"
sources/geo.py       shared "San Francisco, CA + Virtual" -> (mode, country, city) parser
sources/manual.py    your curated spreadsheet -> seed/manual.csv (rows are verified=1, scrapers never overwrite them)
app.py               FastAPI: /api/feed /api/swipe /api/saved /api/stats
static/index.html    the swipe UI (vanilla JS, no build step)
```

## The eligibility rule (models.py → f1_flag)

| Condition                                             | Flag           |
|-------------------------------------------------------|----------------|
| requires US citizen/PR, or organizer says "no intl"   | `not_eligible` |
| off-campus employment / paid as wages                 | `not_eligible` |
| on-campus job                                         | `likely_ok`    |
| stipend (fellowship, research)                        | `check_dso`    |
| prize / free / funding + organizer says intl welcome  | `likely_ok`    |
| prize / free + online                                 | `likely_ok`    |
| prize / free + in person, eligibility unstated        | `check_dso`    |
| conference, free or student-rate, in the US           | `likely_ok`    |
| conference with a scholarship / travel grant          | `check_dso`    |
| conference outside the US                             | `check_dso` (I-20 travel signature) |

Default feed shows `likely_ok` + `check_dso`, online or US-based, not yet swiped, deadline not passed.
Every card carries a "confirm with your DSO" line. This is a heuristic, not immigration advice.

## Feed order

Pure deadline order made the first fifty cards all hackathons (the only category with lots of near-term
deadlines) while research rows with no deadline never surfaced. The feed now puts anything due in the next
two days first, then round-robins across categories: the soonest of each category, then the second of each,
and so on. Rows past their deadline are filtered in the query *and* expired on every server boot, so a stale
database can't show dead cards.

## Filters: majors and communities

`models.derive_fields` maps each row's tags + title onto BadgeUp's ten major sections
(Tech & Computing, Business & Finance, Engineering, Health & Medicine, Sciences & Research, Social Sciences &
Policy, Law & Government, Arts/Humanities/Design, Education, Multidisciplinary) plus communities (Women, Black,
Latinx, LGBTQ+, Native American, First-gen, Beginner friendly). Hackathons and competitions default to Tech.
Stored as a JSON array in `fields`; the API filters with `?field=...` (OR-ed) and `?q=` does free-text search.
To tune it, edit the keyword lists in `MAJORS` / `COMMUNITIES` and rerun `ingest.py`.

## Deadlines mean different things

Every row carries `deadline_label` so the card can say *what* the date is:
`Submissions due` (Devpost, Kaggle, HackerEarth), `Registration closes` (Devfolio), `Applications due` /
`Early bird ends` / `Registration deadline` (BadgeUp, parsed from its free-text cell), `Event starts`
(BadgeUp rows with no explicit deadline), `Apply by` (CSV default; override with a `deadline_label` column).
Rows a source stops listing are retired on the next ingest; rows past their deadline expire automatically.

## Users, sign-in and swipes

Anonymous first: the page generates a UUID per browser (`localStorage.uid`) and sends it as `user` on every
feed / swipe / saved call, so two visitors never see each other's passes. Saves made this way live in that
browser only.

Sign-in is passwordless (`auth.py`): name + email, then a 6-digit code emailed to the address proves it's
real. The sheet opens the first time someone saves a card (once; "Not now" is remembered), from the account
button, or from the banner on the Saved tab. On success the browser's anonymous swipes are merged into the
account (the account's own record wins on conflicts), a 180-day HttpOnly session cookie is set, and every
feed / swipe / saved call resolves to `user_id = 'u:<users.id>'` regardless of what the page sends.

- Codes: 10-minute expiry, 5 wrong attempts, one live code per email, only the sha256 is stored. Limits:
  3 code requests per email per 15 min, 10 per IP per hour, 30 verifies per IP per hour.
- Sending: `RESEND_API_KEY` + `RESEND_FROM` (`mailer.py`). Without the key the code is printed to the server
  log and the sheet tells the user so, which is how local development works. Resend's free tier (about 100
  emails a day) is plenty; verify your domain there so mail comes from `hello@yourdomain`.
- Tables: `users` (email, name, domain, created_at, last_seen), `login_codes`, `sessions`.
- `/admin` → **Users** tab (and `GET /api/admin/users`) lists everyone who signed in, with school domain
  counts and how many cards each has saved. `/api/stats` reports the user count.
- The cookie is `Secure` only when the request arrived over HTTPS (`X-Forwarded-Proto` or the scheme), so it
  works on `http://127.0.0.1` and is locked down behind Caddy/Fly in production.

## Community submissions + review queue

- Anyone can tap **+** in the app and submit a link, name, type, deadline, location, money type and what the
  organizer says about international students. `POST /api/submit` stores it in `submissions` (status `pending`).
  Duplicate URLs (already live or already pending) are acknowledged, not re-queued.
- Spam: a hidden honeypot field (bots that fill it get a fake success), 5 submissions per IP per hour
  (`SUBMIT_LIMIT` / `SUBMIT_WINDOW` in app.py; honours `X-Forwarded-For` behind a proxy), and length caps.
- Review at **/admin**. Every field is editable before approval; approving runs the normal eligibility rule and
  publishes a `verified` opportunity with `source='community'`, so scrapers can never overwrite it.
- Auth: set `ADMIN_TOKEN=some-long-secret` in the server's environment and paste it into the token box on
  /admin (sent as `X-Admin-Token`). Until it's set, admin endpoints only answer requests from localhost and
  the server prints a warning on boot. Set it before you deploy anywhere public.

## Keeping the data fresh in production

`POST /api/admin/ingest` (admin auth) runs `ingest.run()` in a background thread and returns immediately;
`GET /api/admin/ingest` shows whether it's running and the last run's per-source summary and log. Add
`?source=devpost&source=kaggle` to run a subset. Only one run at a time (second call gets 409).

`.github/workflows/ingest.yml` calls it nightly. Set two repo secrets: `APP_URL` (your deployed origin) and
`ADMIN_TOKEN`. The job polls until the run finishes so a failure shows red in the Actions tab. Give the server
`KAGGLE_API_TOKEN` in its environment or the Kaggle source skips.

SQLite runs in WAL mode with a 10 s busy timeout (`models.connect`), so the feed keeps serving while ingest
writes and concurrent swipes wait instead of failing with "database is locked".

## Adding your spreadsheet

Export it to `seed/manual.csv` with the header already in that file (it ships with the header only; rows you
remove from the CSV are retired on the next ingest). Key columns:
`category` (hackathon|conference|fellowship|competition|research|on_campus_job|scholarship|accelerator|other),
`compensation_type` (none|prize|stipend|wage|funding), `requires_us_citizen`, `requires_work_auth`,
`open_to_intl` (yes|no|unknown), `eligibility_note` (what the card shows). Rerun `ingest.py`.

## Adding a source

Create `sources/<name>.py` with `fetch() -> list[Opportunity]`, register it in `sources/__init__.py`.
Use `sources/http.get_json`. Set `source_id` to something stable so re-runs upsert instead of duplicating.

## Kaggle setup (one time)

1. kaggle.com → your avatar → **Settings** → **API** → **Create New Token**. You get a token starting with `KGAT_`.
2. Save it where the scraper looks (Kaggle's own instructions):
   `mkdir -p ~/.kaggle && echo KGAT_xxx > ~/.kaggle/access_token && chmod 600 ~/.kaggle/access_token`
   or `export KAGGLE_API_TOKEN=KGAT_xxx`. The legacy `~/.kaggle/kaggle.json` (username + key) also works.
3. Rerun `ingest.py`. Until then the Kaggle source prints a notice and skips.

"Getting Started" tutorial competitions are skipped. "Recruitment" competitions are kept but the card
says the resulting job offer would still need CPT/OPT.

## Aggregator blogs

Opportunity Desk, OYA Opportunities and Opportunities Corners post single-opportunity articles daily, mostly
open to students worldwide, and all run WordPress, so `sources/aggregators.py` reads their public JSON API
(`/wp-json/wp/v2/posts`, last 90 days, chosen categories) rather than scraping HTML. From each post we keep the
title, the `Deadline:` line, a short excerpt, the featured image and a link to the post (which carries the
official apply link). Posts the three sites share are de-duplicated by title; Opportunities Corners runs first
because its deadlines and locations are the cleanest.

The filters are heuristics, tuned against live posts on 2026-10-22:

- Skipped outright: internships, traineeships, jobs, volunteering, PhD/postdoc/master's funding, high-school
  programs, blog articles, roundup posts ("50 scholarships currently open"), grants for organisations,
  visiting-scholar and professional (journalist, teacher, faculty…) fellowships, and titles aimed at one
  nationality or region. Opportunity Desk tags every post with the regions whose residents may apply; posts
  without the "America" tag are skipped.
- "Scholarship" posts whose title names a university or a degree are skipped (that's "come do a degree here").
- Fellowships and programs must mention students or young people somewhere in the body, else they're treated
  as career fellowships and skipped.
- A body that talks about salary, or a title with a five-figure dollar amount, is marked as employment and
  therefore `not_eligible`.
- Location comes from the title ("… Forum 2027 in Thailand") and is only trusted when it's a known country,
  known city or "City, Country". Unknown stays `unknown` (shown as "Location TBA"), and the default
  US-or-online filter hides in-person events abroad as it does for every source.
- Eligibility: phrases like "all nationalities" / "open to international applicants" set `open_to_intl=yes`;
  "must be a U.S. citizen" sets `requires_us_citizen`. Everything else is `check_dso` with a note to read the
  post's eligibility section.

Expect some noise; the point is volume in the categories the scrapers can't reach. Tune the regexes at the top
of the file and rerun `ingest.py`.

## Reading the official page with Claude (enrich.py)

Scrapers rarely know whether international students may apply; over half the feed used to say
"eligibility isn't stated". `enrich.py` fixes that by reading the organizer's page:

```bash
export ANTHROPIC_API_KEY=sk-ant-...       # or `ant auth login`
.venv/bin/python enrich.py --dry-run --limit 10   # fetch pages, show what would be sent
.venv/bin/python enrich.py --limit 20             # try a few, look at the cards
.venv/bin/python enrich.py                        # the rest (default cap 300 rows per run)
```

For each active, non-verified row whose nationality rule is unknown it fetches the official page
(for aggregator-blog rows, the organizer link inside the post, not the post), trims it to the parts
that mention eligibility, and asks Claude one structured question: does this page say who may take
part? The answer (`yes` / `no` / `depends` / `unknown`, a verbatim quote, a one-line note, whether
it's really a salaried job, and a confidence) goes into the `eligibility_checks` table.
`models.apply_check` merges it into the opportunity: a high- or medium-confidence read overrides
the scraper's guess and recomputes `f1_flag`; the quote shows on the card under "The official page
says"; low-confidence reads are stored but not applied. `upsert` re-applies the check after every
scraper write, so a nightly re-scrape can't erase it. Human-verified rows are never touched.

- Model: `claude-opus-5` by default (`ENRICH_MODEL=claude-haiku-4-5` is a cheaper option for a bulk
  first pass). Each page is roughly 2k-10k input tokens; the system prompt is cached.
- Pages are re-read after 90 days (`--recheck-days`). `--all` also re-verifies rows the scrapers
  already marked yes/no. `--source devpost` limits to one source.
- Devpost (403s), Kaggle and Unstop (client-rendered shells) can't be read and are recorded as
  unreachable without a request; see `SKIP_HOSTS`.
- Nightly: the ingest endpoint accepts `?enrich=true` (the GitHub Actions workflow sends it) and the
  CLI honours `ENRICH=1`. Both silently skip when the server has no `ANTHROPIC_API_KEY`.

## Pathways to Science is opt-in

It's the best research source we have (the only one with an explicit "open to non-U.S. citizens" filter),
but the nonprofit that runs it publishes no reuse policy. `docs/outreach.md` has a permission email;
send it, and once they say yes set `PATHWAYS_OK=1` in the server environment and run ingest. Without that
variable, ingest marks any existing Pathways rows `hidden` so the public feed never shows them; setting it
brings them back on the next run. A run is one request to one public page. Deadlines are not on that page,
so cards say "See program page". The same file has a partnership email for IEFA.org (1,500+ international
scholarships, but personal-use-only terms).

## Roadmap (in order)

1. **Curate first, scrape second.** Load the spreadsheet. Scrapers now cover hackathons, conferences and
   (with a token) Kaggle; fellowships/research/scholarship rows are the moat and need a human eligibility read anyway.
2. **Deadline reminders**: saved items → email/ICS export a week before `deadline`.
3. **Community submissions** with an admin approve queue (sets `verified=1`).
4. **Ranking**: use swipe history (save/pass by category, tags) to order the feed per user.
5. More conference sources: conf.researchr.org (academic CS), university travel-grant pages.
6. Auth + hosting: SQLite → Postgres (Supabase/Neon), FastAPI on Railway/Fly, static UI on Vercel.

## Known source issues

- Devpost 403s without a browser User-Agent + `Accept: application/json`. Rate limit yourself; 20 pages max.
- Devpost in-person events often list only a venue name ("Mitchell Park Community Center"), so country is
  unknown and they stay in the feed. Swipe left or turn on "online only".
- BadgeUp deadlines are free text ("Reg open; early bird Aug 25"). We take the earliest upcoming date that
  isn't after the event, else the event start. Past events expire automatically. Always read the card text.
- challenge.gov's API was unreachable when tested (2026-09-17).
- MLH moved to mlh.com and the events page is now rendered entirely in JavaScript (no event data in the
  HTML, no JSON-LD). Needs a headless browser or their internal API; not wired yet.
- DrivenData has no public competitions API.
- Devfolio is India-heavy in person; the default US-or-online filter hides those.
- Unstop is India-heavy too. We keep only online items, and for "competitions" only those with a cash prize,
  otherwise hundreds of college quizzes flood the feed. Prizes are mostly in rupees.
- The mentorship list carries last cycle's dates for many rows, so they expire until the maintainers update;
  rows with "TBD"/"Varies" deadlines show no date.
- Evaluated and not wired: IEFA (terms), DoraHacks (captcha), Eventbrite (API removed), Luma (paid key),
  ProFellow (login), Topcoder (2 live items), Codeforces/CodeChef (low resume value), HackerRank (robots.txt),
  confs.tech (no grant info; could be added as conference inventory), awesome-grants (PhD-heavy).

## seed/eligibility_checks.csv

A one-off export of the `eligibility_checks` table (116 official pages read on 2026-11-02, done by hand
rather than through the API). The live table is in `opportunities.db`, which is gitignored, so this
copy survives a database rebuild. To restore it into a fresh database:

```bash
sqlite3 opportunities.db ".mode csv" ".import --skip 1 seed/eligibility_checks.csv eligibility_checks"
.venv/bin/python ingest.py manual      # any ingest re-applies the checks to the rows
```
