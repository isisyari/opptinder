"""Normalized opportunity record + the F-1 eligibility rule.

Every source (scraper, API, CSV) produces `Opportunity` objects. `ingest.py`
computes `f1_flag` and upserts them. Keep source-specific parsing in sources/.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "opportunities.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

CATEGORIES = {
    "hackathon", "conference", "fellowship", "competition", "research",
    "on_campus_job", "scholarship", "accelerator", "other",
}


@dataclass
class Opportunity:
    source: str
    source_id: str
    url: str
    title: str
    category: str
    organizer: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    image_url: str | None = None
    deadline: str | None = None          # ISO yyyy-mm-dd
    deadline_label: str | None = None    # "Submissions due" | "Registration closes" | "Event starts" | "Apply by" ...
    starts_at: str | None = None
    ends_at: str | None = None
    location_mode: str = "unknown"       # online | in_person | hybrid | unknown
    country: str | None = None
    city: str | None = None
    compensation_type: str = "none"      # none | prize | stipend | wage | funding
    compensation_note: str | None = None
    requires_us_citizen: bool = False
    requires_work_auth: bool = False
    open_to_intl: str = "unknown"        # yes | no | unknown
    eligibility_note: str | None = None
    verified: bool = False

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r} for {self.title!r}")


import re as _re

# Majors (BadgeUp's sections) and communities, matched as whole words against tags + title.
MAJORS: dict[str, list[str]] = {
    "Tech & Computing": ["tech", "computing", "ai", "machine learning", "software", "swe", "web", "mobile", "cloud",
                         "devops", "cybersecurity", "blockchain", "databases", "serverless", "low/no code", "dev tools",
                         "computer vision", "data science", "quantum", "hpc", "kubernetes", "python", "open source",
                         "artificial intelligence", "developer", "hackathon"],
    "Business & Finance": ["business", "finance", "fintech", "enterprise", "productivity", "marketing", "sales",
                           "e-commerce", "retail", "product", "leadership", "mba", "accounting", "entrepreneur", "startup"],
    "Engineering": ["engineering", "engineers", "iot", "robotic", "robotics", "mobility", "hardware", "energy", "aviation", "aerospace"],
    "Health & Medicine": ["health", "healthcare", "medicine", "medical", "pre-med", "physicians", "nursing", "biomedical", "clinical"],
    "Sciences & Research": ["science", "sciences", "research", "chemistry", "biology", "physics", "earth", "space", "stem",
                            "simulations", "neuroscience", "genomics"],
    "Social Sciences & Policy": ["social sciences", "policy", "political", "sociology", "psychology", "public health",
                                 "economics", "effective altruism", "public policy"],
    "Law & Government": ["law", "legal", "government", "bar", "justice"],
    "Arts, Humanities & Design": ["art", "arts", "design", "humanities", "music", "gaming", "game", "ar/vr", "creative",
                                  "writing", "publishing", "literature", "languages", "film", "media"],
    "Education": ["education", "teaching", "edtech", "learning"],
    "Multidisciplinary / General": ["multidisciplinary", "general", "open ended", "cross-discipline", "social good",
                                    "social impact", "sustainability", "climate"],
}
COMMUNITIES: dict[str, list[str]] = {
    "Women": ["women", "wids", "girls"],
    "Black": ["black", "african"],
    "Latinx": ["latinx", "latino", "latina", "hispanic"],
    "LGBTQ+": ["lgbtq", "lgbtq+", "queer", "ostem", "lesbians", "out & equal"],
    "Native American": ["native american", "indigenous", "aises"],
    "First-gen / underrepresented": ["first-gen", "underrepresented", "diversity"],
    "Beginner friendly": ["beginner", "beginner friendly", "beginners"],
}
_FIELD_RE = {name: _re.compile(r"(?<![\w/])(?:" + "|".join(_re.escape(k) for k in kws) + r")(?![\w/])", _re.I)
             for name, kws in {**MAJORS, **COMMUNITIES}.items()}
_DEFAULT_MAJOR = {"hackathon": "Tech & Computing", "competition": "Tech & Computing"}


def derive_fields(o: Opportunity) -> list[str]:
    hay = " | ".join(o.tags + [o.title])
    out = [name for name, rx in _FIELD_RE.items() if rx.search(hay)]
    default = _DEFAULT_MAJOR.get(o.category)
    if default and default not in out:
        out.insert(0, default)
    return out


def f1_flag(o: Opportunity) -> tuple[str, str]:
    """Return (flag, note). Conservative: only says likely_ok when there's a clear reason.

    This is NOT legal advice; the UI shows a 'confirm with your DSO' line on every card.
    """
    if o.requires_us_citizen or o.open_to_intl == "no":
        return "not_eligible", "Restricted to U.S. citizens / permanent residents."
    if o.requires_work_auth and o.category != "on_campus_job":
        return "not_eligible", "This is off-campus employment; it needs CPT/OPT authorization."
    if o.category == "on_campus_job":
        return "likely_ok", "On-campus employment is generally permitted on F-1 (up to 20 hrs/week in session)."
    if o.compensation_type == "wage":
        return "not_eligible", "Paid as wages; off-campus wages need work authorization."
    if o.category == "conference":
        abroad = o.location_mode != "online" and o.country not in (None, "United States", "USA", "US")
        if abroad:
            return "check_dso", "Attending is not employment, but it's outside the U.S.: you need a valid travel signature on your I-20 to re-enter."
        if o.compensation_type == "funding":
            return "check_dso", "Attending is fine on F-1. The scholarship / travel grant may have citizenship or enrollment rules; read its page."
        return "likely_ok", "Attending a conference is not employment; F-1 status is not an issue."
    if o.compensation_type == "stipend":
        return "check_dso", "Stipend-based. Often fine for fellowships/research, but confirm with your DSO first."
    if o.compensation_type in ("prize", "none", "funding"):
        if o.open_to_intl == "yes":
            return "likely_ok", "Organizer states international participants are welcome."
        if o.location_mode == "online":
            return "likely_ok", "Online and not employment. Prize money is generally not considered work, but check the rules page for country restrictions."
        return "check_dso", "Not employment, but eligibility for international students isn't stated. Check the rules page."
    return "unknown", "Eligibility not determined yet."


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    # lightweight migration for databases created before a column existed
    existing = {r[1] for r in conn.execute("PRAGMA table_info(opportunities)")}
    for col, ddl in {"fields": "TEXT NOT NULL DEFAULT '[]'", "deadline_label": "TEXT"}.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE opportunities ADD COLUMN {col} {ddl}")
    return conn


def upsert(conn: sqlite3.Connection, o: Opportunity) -> None:
    flag, note = f1_flag(o)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = asdict(o)
    row["tags"] = json.dumps(o.tags)
    row["fields"] = json.dumps(derive_fields(o))
    row["f1_flag"] = flag
    row["eligibility_note"] = o.eligibility_note or note
    row["requires_us_citizen"] = int(o.requires_us_citizen)
    row["requires_work_auth"] = int(o.requires_work_auth)
    row["verified"] = int(o.verified)
    row["first_seen"] = now
    row["last_seen"] = now
    cols = ", ".join(row)
    placeholders = ", ".join(f":{k}" for k in row)
    # On conflict refresh everything except first_seen. A scraper (verified=0) must not
    # overwrite a human-verified row, but the curated CSV (verified=1) can update itself.
    updates = ", ".join(
        f"{k}=excluded.{k}" for k in row
        if k not in ("first_seen", "source", "source_id")
    )
    conn.execute(
        f"""INSERT INTO opportunities ({cols}) VALUES ({placeholders})
            ON CONFLICT(source, source_id) DO UPDATE SET {updates}
            WHERE opportunities.verified = 0 OR excluded.verified = 1""",
        row,
    )
    conn.execute(
        "UPDATE opportunities SET last_seen=?, status='active' WHERE source=? AND source_id=?",
        (now, o.source, o.source_id),
    )


def expire_past_deadlines(conn: sqlite3.Connection) -> int:
    today = date.today().isoformat()
    cur = conn.execute(
        "UPDATE opportunities SET status='expired' WHERE status='active' AND deadline IS NOT NULL AND deadline < ?",
        (today,),
    )
    return cur.rowcount
