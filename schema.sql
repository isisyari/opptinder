-- Opportunities feed for F-1 students.
-- Eligibility is first-class: the product's whole value is knowing whether an
-- international student on F-1 can actually take part without CPT/OPT.

CREATE TABLE IF NOT EXISTS opportunities (
    id                  INTEGER PRIMARY KEY,
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

-- Community submissions. Anyone can submit; an admin approves, which creates a
-- verified opportunity with source='community' and source_id=submission id.
CREATE TABLE IF NOT EXISTS submissions (
    id              INTEGER PRIMARY KEY,
    payload         TEXT NOT NULL,              -- JSON of the Opportunity fields as submitted (or as edited on approval)
    submitted_by    TEXT,                       -- optional email / handle
    note            TEXT,                       -- submitter's note to the reviewer
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    review_note     TEXT,
    created_at      TEXT NOT NULL,
    reviewed_at     TEXT
);

-- Swipe log. user_id is a free string so this works before you add auth.
CREATE TABLE IF NOT EXISTS swipes (
    id              INTEGER PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'local',
    opportunity_id  INTEGER NOT NULL REFERENCES opportunities(id),
    action          TEXT NOT NULL,              -- save | pass
    created_at      TEXT NOT NULL,
    UNIQUE (user_id, opportunity_id)
);
