"""API + static swipe UI.

    uvicorn app:app --reload
    open http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import ingest as ingest_mod
import mailer
from auth import client_ip, current_user_id, rate_limit, router as auth_router
from models import CATEGORIES, Opportunity, connect, expire_past_deadlines, upsert


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not ADMIN_TOKEN:
        print("[startup] WARNING: ADMIN_TOKEN is not set. Admin endpoints (review queue, ingest) answer "
              "localhost only. Set ADMIN_TOKEN before exposing this server publicly.")
    if not mailer.configured():
        print("[startup] no email provider (BREVO_API_KEY or RESEND_API_KEY): sign-in codes are printed to this log instead of emailed.")
    # Expiry used to run only inside ingest.py, so a stale DB served dead cards for weeks.
    # Do it on every boot too; the feed query below also filters by date as a belt-and-braces.
    with connect() as conn:
        n = expire_past_deadlines(conn)
        conn.commit()
    if n:
        print(f"[startup] expired {n} past-deadline opportunities")
    yield


app = FastAPI(title="F-1 Opportunities", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
FLAGS = {"likely_ok", "check_dso", "unknown", "not_eligible"}
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
SUBMIT_LIMIT = 5                 # community submissions per IP ...
SUBMIT_WINDOW = 60 * 60          # ... per hour


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_admin(request: Request, x_admin_token: str | None = Header(default=None)):
    """If ADMIN_TOKEN is set, require it. Otherwise only allow requests from this machine."""
    if ADMIN_TOKEN:
        if x_admin_token != ADMIN_TOKEN:
            raise HTTPException(401, "bad admin token")
        return
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "admin is localhost-only until ADMIN_TOKEN is set")


app.include_router(auth_router)


def _row(r) -> dict:
    d = dict(r)
    d["tags"] = json.loads(d["tags"] or "[]")
    d["fields"] = json.loads(d.get("fields") or "[]")
    return d


@app.get("/api/feed")
def feed(
    user: str = Depends(current_user_id),               # account if signed in, else the browser's anonymous id
    category: list[str] | None = Query(default=None),
    field: list[str] | None = Query(default=None),      # majors / communities, OR-ed together
    q: str | None = None,                               # free-text search
    flags: str = "likely_ok,check_dso",
    online_only: bool = False,
    us_or_online: bool = True,
    limit: int = 30,
):
    wanted = {f for f in flags.split(",") if f in FLAGS} or {"likely_ok", "check_dso"}
    flag_list = ",".join(f"'{f}'" for f in wanted)
    sql = f"""
        SELECT * FROM opportunities o
        WHERE o.status = 'active'
          AND (o.deadline IS NULL OR o.deadline >= %(today)s)
          AND o.id NOT IN (SELECT opportunity_id FROM swipes WHERE user_id = %(user)s)
          AND o.f1_flag IN ({flag_list})
    """
    params: dict = {"user": user, "today": date.today().isoformat()}
    if category:
        sql += " AND o.category = ANY(%(category)s)"
        params["category"] = list(category)
    if field:
        sql += " AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(o.fields::jsonb) v WHERE v = ANY(%(field)s))"
        params["field"] = list(field)
    if q and q.strip():
        sql += " AND (o.title ILIKE %(q)s OR o.description ILIKE %(q)s OR o.tags ILIKE %(q)s OR o.organizer ILIKE %(q)s)"
        params["q"] = f"%{q.strip()}%"
    if online_only:
        sql += " AND o.location_mode = 'online'"
    elif us_or_online:
        # hybrid counts as reachable; unknown location (e.g. "Location TBA") is kept rather than hidden
        sql += """ AND (o.location_mode IN ('online', 'hybrid', 'unknown')
                        OR o.country IN ('United States', 'USA', 'US') OR o.country IS NULL)"""
    # Mix the deck. Pure deadline order made the first 50 cards all hackathons (the only category with
    # lots of near-term deadlines) while research rows with no deadline never surfaced. Instead:
    #   1. anything due in the next 2 days comes first, so nothing urgent is buried;
    #   2. then round-robin across categories (soonest of each, then second of each, ...).
    sql = f"""
        SELECT * FROM (
            SELECT o.*,
                   ROW_NUMBER() OVER (PARTITION BY o.category
                                      ORDER BY (o.deadline IS NULL), o.deadline, (o.f1_flag <> 'likely_ok'), o.verified DESC, o.id) AS rn,
                   (o.deadline IS NOT NULL AND o.deadline <= (%(today)s::date + 2)::text) AS urgent
            FROM ({sql}) AS o
        ) AS ranked
        ORDER BY urgent DESC, rn, (deadline IS NULL), deadline, id
        LIMIT %(limit)s"""
    params["limit"] = limit
    with connect() as conn:
        rows = []
        for r in conn.execute(sql, params):
            d = _row(r)
            d.pop("rn", None); d.pop("urgent", None)
            rows.append(d)
        return rows


class Swipe(BaseModel):
    opportunity_id: int
    action: str  # save | pass
    user: str = "local"


@app.post("/api/swipe")
def swipe(s: Swipe, request: Request):
    if s.action not in ("save", "pass"):
        raise HTTPException(400, "action must be save or pass")
    uid = current_user_id(request, s.user)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """INSERT INTO swipes (user_id, opportunity_id, action, created_at) VALUES (%s,%s,%s,%s)
               ON CONFLICT (user_id, opportunity_id) DO UPDATE SET action=EXCLUDED.action, created_at=EXCLUDED.created_at""",
            (uid, s.opportunity_id, s.action, now),
        )
    return {"ok": True}


@app.get("/api/saved")
def saved(user: str = Depends(current_user_id)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT o.* FROM opportunities o JOIN swipes s ON s.opportunity_id = o.id
               WHERE s.user_id = %s AND s.action = 'save'
               ORDER BY (o.deadline IS NULL), o.deadline ASC""",
            (user,),
        )
        return [_row(r) for r in rows]


@app.delete("/api/swipe/{opportunity_id}")
def unswipe(opportunity_id: int, user: str = Depends(current_user_id)):
    with connect() as conn:
        conn.execute("DELETE FROM swipes WHERE user_id=%s AND opportunity_id=%s", (user, opportunity_id))
    return {"ok": True}


@app.get("/api/stats")
def stats():
    def counts(conn, sql):
        return {r["k"]: r["n"] for r in conn.execute(sql)}
    with connect() as conn:
        return {
            "by_source": counts(conn, "SELECT source AS k, COUNT(*) AS n FROM opportunities WHERE status='active' GROUP BY source"),
            "by_flag": counts(conn, "SELECT f1_flag AS k, COUNT(*) AS n FROM opportunities WHERE status='active' GROUP BY f1_flag"),
            "by_category": counts(conn, "SELECT category AS k, COUNT(*) AS n FROM opportunities WHERE status='active' GROUP BY category"),
            "by_field": counts(conn, "SELECT v AS k, COUNT(DISTINCT o.id) AS n FROM opportunities o, jsonb_array_elements_text(o.fields::jsonb) v "
                                     "WHERE o.status='active' GROUP BY v"),
            "total_active": conn.execute("SELECT COUNT(*) AS n FROM opportunities WHERE status='active'").fetchone()["n"],
            "users": conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"],
        }


# ---------------------------------------------------------------- community submissions

class SubmissionIn(BaseModel):
    url: str
    title: str
    category: str
    organizer: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    deadline: str | None = None
    deadline_label: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    location_mode: str = "unknown"
    country: str | None = None
    city: str | None = None
    compensation_type: str = "none"
    compensation_note: str | None = None
    requires_us_citizen: bool = False
    requires_work_auth: bool = False
    open_to_intl: str = "unknown"
    eligibility_note: str | None = None
    # meta (not part of the opportunity)
    submitted_by: str | None = None
    note: str | None = None
    website: str | None = None   # honeypot; humans never see the field


_MAX_LEN = {"url": 500, "title": 200, "organizer": 200, "description": 2000, "compensation_note": 200,
            "eligibility_note": 500, "country": 100, "city": 100, "note": 2000, "submitted_by": 200}


def _validate(s: SubmissionIn) -> None:
    for f, n in _MAX_LEN.items():
        v = getattr(s, f)
        if v and len(v) > n:
            raise HTTPException(400, f"{f} is too long (max {n} characters)")
    if len(s.tags) > 20 or any(len(t) > 50 for t in s.tags):
        raise HTTPException(400, "too many / too long tags")
    if not s.url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http:// or https://")
    if not s.title.strip():
        raise HTTPException(400, "title is required")
    if s.category not in CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(CATEGORIES)}")
    if s.location_mode not in ("online", "in_person", "hybrid", "unknown"):
        raise HTTPException(400, "bad location_mode")
    if s.compensation_type not in ("none", "prize", "stipend", "wage", "funding"):
        raise HTTPException(400, "bad compensation_type")
    if s.open_to_intl not in ("yes", "no", "unknown"):
        raise HTTPException(400, "bad open_to_intl")
    for f in ("deadline", "starts_at", "ends_at"):
        v = getattr(s, f)
        if v:
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, f"{f} must be yyyy-mm-dd")


def _payload(s: SubmissionIn) -> dict:
    d = s.model_dump()
    d.pop("submitted_by", None)
    d.pop("note", None)
    d.pop("website", None)
    d["title"] = d["title"].strip()
    d["url"] = d["url"].strip()
    d["tags"] = [t.strip() for t in d["tags"] if t and t.strip()]
    return d


def _sub_row(r) -> dict:
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    return d


@app.post("/api/submit")
def submit(s: SubmissionIn, request: Request):
    if s.website:                    # bot filled the hidden field: pretend it worked, store nothing
        return {"ok": True, "id": 0}
    rate_limit(f"submit:{client_ip(request)}", SUBMIT_LIMIT, SUBMIT_WINDOW,
               "Too many submissions from this address; try again in an hour.")
    _validate(s)
    with connect() as conn:
        dup = conn.execute("SELECT id, title FROM opportunities WHERE url = %s AND status = 'active'", (s.url.strip(),)).fetchone()
        if dup:
            return {"ok": True, "duplicate": True, "title": dup["title"]}
        pending = conn.execute("SELECT id FROM submissions WHERE status='pending' AND (payload::jsonb->>'url') = %s", (s.url.strip(),)).fetchone()
        if pending:
            return {"ok": True, "duplicate": True, "pending": True}
        sid = conn.execute(
            "INSERT INTO submissions (payload, submitted_by, note, created_at) VALUES (%s,%s,%s,%s) RETURNING id",
            (json.dumps(_payload(s)), (s.submitted_by or "").strip() or None, (s.note or "").strip() or None, _now()),
        ).fetchone()["id"]
    return {"ok": True, "id": sid}


@app.get("/api/admin/submissions", dependencies=[Depends(require_admin)])
def list_submissions(status: str = "pending"):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM submissions WHERE status = %s ORDER BY created_at DESC", (status,))
        return [_sub_row(r) for r in rows]


@app.post("/api/admin/submissions/{sid}/approve", dependencies=[Depends(require_admin)])
def approve(sid: int, edited: SubmissionIn | None = None):
    with connect() as conn:
        row = conn.execute("SELECT * FROM submissions WHERE id = %s", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "no such submission")
        if edited is not None:
            _validate(edited)
            payload = _payload(edited)
        else:
            payload = json.loads(row["payload"])
        o = Opportunity(source="community", source_id=str(sid), verified=True, **payload)
        upsert(conn, o)
        conn.execute(
            "UPDATE submissions SET status='approved', payload=%s, reviewed_at=%s WHERE id=%s",
            (json.dumps(payload), _now(), sid),
        )
        opp = conn.execute("SELECT id, f1_flag, eligibility_note FROM opportunities WHERE source='community' AND source_id=%s", (str(sid),)).fetchone()
    return {"ok": True, "opportunity_id": opp["id"], "f1_flag": opp["f1_flag"], "eligibility_note": opp["eligibility_note"]}


class Reject(BaseModel):
    reason: str | None = None


@app.post("/api/admin/submissions/{sid}/reject", dependencies=[Depends(require_admin)])
def reject(sid: int, body: Reject | None = None):
    with connect() as conn:
        cur = conn.execute(
            "UPDATE submissions SET status='rejected', review_note=%s, reviewed_at=%s WHERE id=%s AND status='pending'",
            ((body.reason if body else None), _now(), sid),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "no pending submission with that id")
    return {"ok": True}


@app.get("/api/admin/users", dependencies=[Depends(require_admin)])
def list_users():
    """Who has signed in: name, email, school domain, join date, how many cards they've saved."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT u.id, u.name, u.email, u.domain, u.created_at, u.last_seen,
                      (SELECT COUNT(*) FROM swipes s WHERE s.user_id = 'u:' || u.id::text AND s.action = 'save') AS saved,
                      (SELECT COUNT(*) FROM swipes s WHERE s.user_id = 'u:' || u.id::text) AS swipes
               FROM users u ORDER BY u.created_at DESC""").fetchall()
        by_domain = {r["k"]: r["n"] for r in conn.execute("SELECT domain AS k, COUNT(*) AS n FROM users GROUP BY domain ORDER BY 2 DESC")}
    return {"users": [dict(r) for r in rows], "by_domain": by_domain}


# ---------------------------------------------------------------- on-demand ingest
# POST /api/admin/ingest starts a run in a background thread and returns at once (a full run takes
# minutes). GET /api/admin/ingest reports the last run. Useful on a long-lived server (a VM); on
# Vercel the function is frozen after the response, so the nightly GitHub Actions workflow runs
# ingest.py directly against the database instead.

_ingest_lock = threading.Lock()
_ingest_state: dict = {"running": False, "last": None, "log": []}


def _ingest_worker(names: list[str] | None, enrich: bool) -> None:
    lines: list[str] = []
    try:
        summary = ingest_mod.run(names, log=lambda s: lines.append(s), enrich=enrich)
    except Exception as e:                    # a bug in ingest must not take the server down
        summary = {"error": f"{type(e).__name__}: {e}"}
    finally:
        _ingest_state.update(running=False, last=summary, log=lines[-200:])
        _ingest_lock.release()


@app.post("/api/admin/ingest", dependencies=[Depends(require_admin)])
def start_ingest(source: list[str] | None = Query(default=None), enrich: bool = False):
    """enrich=true also runs enrich.py afterwards (needs ANTHROPIC_API_KEY on the server)."""
    if not _ingest_lock.acquire(blocking=False):
        raise HTTPException(409, "an ingest run is already in progress")
    _ingest_state.update(running=True, started=_now())
    threading.Thread(target=_ingest_worker, args=(source, enrich), daemon=True, name="ingest").start()
    return {"ok": True, "started": _ingest_state["started"], "sources": source or "all", "enrich": enrich}


@app.get("/api/admin/ingest", dependencies=[Depends(require_admin)])
def ingest_status():
    return _ingest_state


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.png", include_in_schema=False)
@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def no_icon():
    return Response(status_code=204)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
