"""Normalized opportunity record + the F-1 eligibility rule.

Every source (scraper, API, CSV) produces `Opportunity` objects. `ingest.py`
computes `f1_flag` and upserts them. Keep source-specific parsing in sources/.
"""
from __future__ import annotations

import atexit
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, asdict
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).parent
SCHEMA_PATH = ROOT / "schema.sql"


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from ./.env into the environment (local development convenience).
    Existing environment variables win. Production (Vercel, GitHub Actions) sets real env vars."""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

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


_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Put your Postgres connection string in .env "
                           "(local) or the host's environment (Vercel / GitHub Actions).")
    return url


def pool() -> ConnectionPool:
    """One small connection pool per process. The schema is created (IF NOT EXISTS) on first use,
    so a brand-new database works with no separate migration step."""
    global _pool
    with _pool_lock:
        if _pool is None:
            # prepare_threshold=None: no server-side prepared statements, so the pooled
            # (pgbouncer-style) connection strings from Neon and Supabase both work.
            p = ConnectionPool(database_url(), min_size=0, max_size=4, open=True,
                               kwargs={"row_factory": dict_row, "prepare_threshold": None},
                               check=ConnectionPool.check_connection)
            with p.connection() as conn:
                conn.execute(SCHEMA_PATH.read_text())
            _pool = p
            atexit.register(p.close)        # clean shutdown for scripts (ingest, enrich, migrations)
    return _pool


@contextmanager
def connect():
    """`with connect() as conn:` — commits on clean exit, rolls back on error, returns the
    connection to the pool. Rows come back as dicts."""
    with pool().connection() as conn:
        yield conn


Connection = psycopg.Connection


def upsert(conn: Connection, o: Opportunity) -> None:
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
    placeholders = ", ".join(f"%({k})s" for k in row)
    # On conflict refresh everything except first_seen. A scraper (verified=0) must not
    # overwrite a human-verified row, but the curated CSV (verified=1) can update itself.
    updates = ", ".join(
        f"{k}=EXCLUDED.{k}" for k in row
        if k not in ("first_seen", "source", "source_id")
    )
    conn.execute(
        f"""INSERT INTO opportunities ({cols}) VALUES ({placeholders})
            ON CONFLICT (source, source_id) DO UPDATE SET {updates}
            WHERE opportunities.verified = 0 OR EXCLUDED.verified = 1""",
        row,
    )
    conn.execute(
        "UPDATE opportunities SET last_seen=%s, status='active' WHERE source=%s AND source_id=%s",
        (now, o.source, o.source_id),
    )
    apply_check(conn, o.source, o.source_id)


_OPP_FIELDS = {f.name for f in fields(Opportunity)}


def apply_check(conn: Connection, source: str, source_id: str) -> bool:
    """Merge what enrich.py learned from the official page into the opportunity row and recompute
    its flag. Human-verified rows are never touched. Returns True if the row changed."""
    row = conn.execute("SELECT * FROM opportunities WHERE source=%s AND source_id=%s", (source, source_id)).fetchone()
    chk = conn.execute("SELECT * FROM eligibility_checks WHERE source=%s AND source_id=%s", (source, source_id)).fetchone()
    if not row or not chk or row["verified"]:
        return False
    if not chk["fetch_ok"]:
        conn.execute("UPDATE opportunities SET elig_checked_at=NULL, elig_quote=NULL WHERE id=%s", (row["id"],))
        return False
    data = {k: row[k] for k in _OPP_FIELDS if k in row}
    data["tags"] = json.loads(row["tags"] or "[]")
    data["requires_us_citizen"] = bool(row["requires_us_citizen"])
    data["requires_work_auth"] = bool(row["requires_work_auth"])
    data["verified"] = False
    data["eligibility_note"] = None            # let f1_flag write the note, then override below
    o = Opportunity(**data)

    # A trusted read of the actual page beats every scraper heuristic (regexes over a blog post,
    # "invite only" flags), so it fully determines the nationality / employment fields. That also
    # makes re-checks idempotent. Low-confidence reads leave the scraper's values alone.
    trusted = chk["confidence"] in ("high", "medium")
    if trusted:
        o.open_to_intl = chk["international_ok"] if chk["international_ok"] in ("yes", "no") else "unknown"
        o.requires_us_citizen = chk["international_ok"] == "no" and chk["restriction"] == "us_citizen_or_pr"
        if o.category != "on_campus_job":
            o.requires_work_auth = bool(chk["is_paid_job"])
    flag, note = f1_flag(o)
    if trusted and o.open_to_intl == "no" and not o.requires_us_citizen:
        note = chk["note"] or "The organizer restricts entry to a specific group; read the rules page."
    elif trusted and chk["international_ok"] == "depends":
        note = (chk["note"] or "Open only to certain nationalities; check the rules page.") + " Confirm with your DSO."
    elif chk["international_ok"] == "unknown" and flag != "not_eligible":
        note = "We read the official page and it doesn't state nationality or visa rules. " + note
    if trusted and chk["audience"] in ("professionals", "high_school"):
        note += (" Note: the page reads as aimed at working professionals rather than enrolled students."
                 if chk["audience"] == "professionals" else " Note: the page reads as aimed at high-school students.")
    conn.execute(
        """UPDATE opportunities SET open_to_intl=%s, requires_us_citizen=%s, requires_work_auth=%s, f1_flag=%s,
           eligibility_note=%s, elig_quote=%s, elig_checked_at=%s WHERE id=%s""",
        (o.open_to_intl, int(o.requires_us_citizen), int(o.requires_work_auth), flag, note,
         (chk["quote"] or None) if trusted else None, chk["checked_at"], row["id"]),
    )
    return True


def expire_past_deadlines(conn: Connection) -> int:
    today = date.today().isoformat()
    cur = conn.execute(
        "UPDATE opportunities SET status='expired' WHERE status='active' AND deadline IS NOT NULL AND deadline < %s",
        (today,),
    )
    return cur.rowcount
