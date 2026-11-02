-- Opportunities feed for F-1 students (PostgreSQL; Neon's free tier in production).
-- Eligibility is first-class: the product's whole value is knowing whether an
-- international student on F-1 can actually take part without CPT/OPT.
-- Dates are ISO text ('2026-11-02'), timestamps ISO-8601 text; 0/1 integers stand in for booleans.

CREATE TABLE IF NOT EXISTS opportunities (
    id                  SERIAL PRIMARY KEY,
    source              TEXT NOT NULL,          -- devpost | devfolio | manual | ...
    source_id           TEXT NOT NULL,          -- id inside that source
    url                 TEXT NOT NULL,
    title               TEXT NOT NULL,
    organizer           TEXT,
    category            TEXT NOT NULL,          -- hackathon | conference | fellowship | competition |
                                                -- research | on_campus_job | scholarship | accelerator | other
    description         TEXT,
    tags                TEXT,                   -- JSON array of strings
    image_url           TEXT,

    fields              TEXT NOT NULL DEFAULT '[]',  -- JSON array: majors + communities, derived from tags (see models.derive_fields)

    -- when / where
    deadline            TEXT,                   -- ISO date, application/submission deadline
    deadline_label      TEXT,                   -- what the deadline means: "Submissions due", "Registration closes", "Event starts"
    starts_at           TEXT,                   -- ISO date
    ends_at             TEXT,                   -- ISO date
    location_mode       TEXT NOT NULL DEFAULT 'unknown',  -- online | in_person | hybrid | unknown
    country             TEXT,                   -- ISO-ish country name, NULL for online-only
    city                TEXT,

    -- money
    compensation_type   TEXT NOT NULL DEFAULT 'none',     -- none | prize | stipend | wage | funding
    compensation_note   TEXT,                   -- "$740,000 in prizes", "$6,000 stipend"

    -- F-1 eligibility (the differentiator)
    requires_us_citizen INTEGER NOT NULL DEFAULT 0,       -- 1 if citizen/PR only
    requires_work_auth  INTEGER NOT NULL DEFAULT 0,       -- 1 if it's employment needing CPT/OPT/EAD
    open_to_intl        TEXT NOT NULL DEFAULT 'unknown',  -- yes | no | unknown (explicit statement from organizer)
    f1_flag             TEXT NOT NULL DEFAULT 'unknown',  -- likely_ok | check_dso | not_eligible | unknown  (computed)
    eligibility_note    TEXT,                   -- human-readable reason shown on the card
    elig_quote          TEXT,                   -- verbatim sentence from the official page (enrich.py)
    elig_checked_at     TEXT,                   -- when that page was read

    -- bookkeeping
    status              TEXT NOT NULL DEFAULT 'active',   -- active | expired | hidden
    verified            INTEGER NOT NULL DEFAULT 0,       -- 1 once a human checked eligibility
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,

    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_opp_deadline ON opportunities (deadline);
CREATE INDEX IF NOT EXISTS idx_opp_category ON opportunities (category);
CREATE INDEX IF NOT EXISTS idx_opp_flag     ON opportunities (f1_flag);
CREATE INDEX IF NOT EXISTS idx_opp_status   ON opportunities (status);

-- Community submissions. Anyone can submit; an admin approves, which creates a
-- verified opportunity with source='community' and source_id=submission id.
CREATE TABLE IF NOT EXISTS submissions (
    id              SERIAL PRIMARY KEY,
    payload         TEXT NOT NULL,              -- JSON of the Opportunity fields as submitted (or as edited on approval)
    submitted_by    TEXT,                       -- optional email / handle
    note            TEXT,                       -- submitter's note to the reviewer
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    review_note     TEXT,
    created_at      TEXT NOT NULL,
    reviewed_at     TEXT
);

-- What Claude read on the official page (enrich.py). Kept separately from opportunities so a
-- scraper re-run can't wipe it: models.upsert re-applies the check to the row after every write.
CREATE TABLE IF NOT EXISTS eligibility_checks (
    source              TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    checked_url         TEXT,                       -- the page that was actually read
    fetch_ok            INTEGER NOT NULL DEFAULT 1, -- 0 = page unreachable / empty, nothing learned
    international_ok    TEXT NOT NULL,              -- yes | no | depends | unknown
    restriction         TEXT,                       -- none | us_citizen_or_pr | specific_nationalities | region_residents | other
    audience            TEXT,                       -- students | professionals | high_school | anyone | unknown
    quote               TEXT,                       -- verbatim sentence from the page, shown on the card
    note                TEXT,                       -- one-line plain-English summary of the restriction
    is_paid_job         INTEGER NOT NULL DEFAULT 0, -- page describes salaried / hourly employment
    confidence          TEXT,                       -- high | medium | low
    model               TEXT,
    checked_at          TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);

-- Accounts (passwordless: name + email, verified by a 6-digit code sent to the address).
-- Signed-in users' swipes are stored under user_id = 'u:<users.id>'; anonymous browsers use their UUID.
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,       -- lowercased
    name            TEXT NOT NULL,
    domain          TEXT NOT NULL,              -- part after @, for "which schools use this"
    created_at      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_codes (
    id              SERIAL PRIMARY KEY,
    email           TEXT NOT NULL,
    name            TEXT NOT NULL,              -- carried to users.name on first sign-in
    code_hash       TEXT NOT NULL,              -- sha256 of the 6 digits; the code itself is never stored
    attempts        INTEGER NOT NULL DEFAULT 0,
    consumed        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_codes_email ON login_codes (email, created_at);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash      TEXT PRIMARY KEY,           -- sha256 of the cookie value
    user_id         INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

-- Swipe log. user_id is a free string: anonymous UUID or 'u:<users.id>'.
CREATE TABLE IF NOT EXISTS swipes (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'local',
    opportunity_id  INTEGER NOT NULL REFERENCES opportunities(id),
    action          TEXT NOT NULL,              -- save | pass
    created_at      TEXT NOT NULL,
    UNIQUE (user_id, opportunity_id)
);
CREATE INDEX IF NOT EXISTS idx_swipes_user ON swipes (user_id);
