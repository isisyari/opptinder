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

Default feed shows `likely_ok` + `check_dso`, online or US-based, not yet swiped, soonest deadline first.
Every card carries a "confirm with your DSO" line. This is a heuristic, not immigration advice.

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

## Community submissions + review queue

- Anyone can tap **+** in the app and submit a link, name, type, deadline, location, money type and what the
  organizer says about international students. `POST /api/submit` stores it in `submissions` (status `pending`).
  Duplicate URLs (already live or already pending) are acknowledged, not re-queued.
- Review at **/admin**. Every field is editable before approval; approving runs the normal eligibility rule and
  publishes a `verified` opportunity with `source='community'`, so scrapers can never overwrite it.
- Auth: set `ADMIN_TOKEN=some-long-secret` in the server's environment and paste it into the token box on
  /admin (sent as `X-Admin-Token`). Until it's set, admin endpoints only answer requests from localhost.
  Set it before you deploy anywhere public.

## Adding your spreadsheet

Export it to `seed/manual.csv` with the header already in that file. Key columns:
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

## Pathways to Science is opt-in

It's the best research source we have (the only one with an explicit "open to non-U.S. citizens" filter),
but the nonprofit that runs it publishes no reuse policy. `docs/outreach.md` has a permission email;
send it, and once they say yes run ingest with `PATHWAYS_OK=1 python3 ingest.py`. A run is one request
to one public page. Deadlines are not on that page, so cards say "See program page". The same file has a
partnership email for IEFA.org (1,500+ international scholarships, but personal-use-only terms).

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
