"""API + static swipe UI.

    uvicorn app:app --reload
    open http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from models import CATEGORIES, Opportunity, connect, upsert

app = FastAPI(title="F-1 Opportunities")
STATIC = Path(__file__).parent / "static"
FLAGS = {"likely_ok", "check_dso", "unknown", "not_eligible"}
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()


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


def _row(r) -> dict:
    d = dict(r)
    d["tags"] = json.loads(d["tags"] or "[]")
    d["fields"] = json.loads(d.get("fields") or "[]")
    return d


@app.get("/api/feed")
def feed(
    user: str = "local",
    category: list[str] | None = Query(default=None),
    field: list[str] | None = Query(default=None),      # majors / communities, OR-ed together
    q: str | None = None,                               # free-text search
    flags: str = "likely_ok,check_dso",
    online_only: bool = False,
    us_or_online: bool = True,
    limit: int = 30,
):
    wanted = {f for f in flags.split(",") if f in FLAGS} or {"likely_ok", "check_dso"}
    sql = """
        SELECT o.* FROM opportunities o
        WHERE o.status = 'active'
          AND o.id NOT IN (SELECT opportunity_id FROM swipes WHERE user_id = :user)
          AND o.f1_flag IN (%s)
    """ % ",".join(f"'{f}'" for f in wanted)
    params: dict = {"user": user}
    if category:
        sql += " AND o.category IN (%s)" % ",".join(f":c{i}" for i in range(len(category)))
        params.update({f"c{i}": c for i, c in enumerate(category)})
    if field:
        sql += " AND EXISTS (SELECT 1 FROM json_each(o.fields) WHERE value IN (%s))" % ",".join(f":f{i}" for i in range(len(field)))
        params.update({f"f{i}": f for i, f in enumerate(field)})
    if q and q.strip():
        sql += " AND (o.title LIKE :q OR o.description LIKE :q OR o.tags LIKE :q OR o.organizer LIKE :q)"
        params["q"] = f"%{q.strip()}%"
    if online_only:
        sql += " AND o.location_mode = 'online'"
    elif us_or_online:
        # hybrid counts as reachable; unknown location (e.g. "Location TBA") is kept rather than hidden
        sql += """ AND (o.location_mode IN ('online', 'hybrid', 'unknown')
                        OR o.country IN ('United States', 'USA', 'US') OR o.country IS NULL)"""
    sql += " ORDER BY (o.deadline IS NULL), o.deadline ASC LIMIT :limit"
    params["limit"] = limit
    with connect() as conn:
        return [_row(r) for r in conn.execute(sql, params)]


class Swipe(BaseModel):
    opportunity_id: int
    action: str  # save | pass
    user: str = "local"


@app.post("/api/swipe")
def swipe(s: Swipe):
    if s.action not in ("save", "pass"):
        raise HTTPException(400, "action must be save or pass")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """INSERT INTO swipes (user_id, opportunity_id, action, created_at) VALUES (?,?,?,?)
               ON CONFLICT(user_id, opportunity_id) DO UPDATE SET action=excluded.action, created_at=excluded.created_at""",
            (s.user, s.opportunity_id, s.action, now),
        )
    return {"ok": True}


@app.get("/api/saved")
def saved(user: str = "local"):
    with connect() as conn:
        rows = conn.execute(
            """SELECT o.* FROM opportunities o JOIN swipes s ON s.opportunity_id = o.id
               WHERE s.user_id = ? AND s.action = 'save'
               ORDER BY (o.deadline IS NULL), o.deadline ASC""",
            (user,),
        )
        return [_row(r) for r in rows]


@app.delete("/api/swipe/{opportunity_id}")
def unswipe(opportunity_id: int, user: str = "local"):
    with connect() as conn:
        conn.execute("DELETE FROM swipes WHERE user_id=? AND opportunity_id=?", (user, opportunity_id))
    return {"ok": True}


@app.get("/api/stats")
def stats():
    with connect() as conn:
        return {
            "by_source": {r[0]: r[1] for r in conn.execute("SELECT source, COUNT(*) FROM opportunities WHERE status='active' GROUP BY source")},
            "by_flag": {r[0]: r[1] for r in conn.execute("SELECT f1_flag, COUNT(*) FROM opportunities WHERE status='active' GROUP BY f1_flag")},
            "by_category": {r[0]: r[1] for r in conn.execute("SELECT category, COUNT(*) FROM opportunities WHERE status='active' GROUP BY category")},
            "by_field": {r[0]: r[1] for r in conn.execute(
                "SELECT j.value, COUNT(DISTINCT o.id) FROM opportunities o, json_each(o.fields) j WHERE o.status='active' GROUP BY j.value")},
            "total_active": conn.execute("SELECT COUNT(*) FROM opportunities WHERE status='active'").fetchone()[0],
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


def _validate(s: SubmissionIn) -> None:
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
    d["title"] = d["title"].strip()
    d["url"] = d["url"].strip()
    d["tags"] = [t.strip() for t in d["tags"] if t and t.strip()]
    return d


def _sub_row(r) -> dict:
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    return d


@app.post("/api/submit")
def submit(s: SubmissionIn):
    _validate(s)
    with connect() as conn:
        dup = conn.execute("SELECT id, title FROM opportunities WHERE url = ? AND status = 'active'", (s.url.strip(),)).fetchone()
        if dup:
            return {"ok": True, "duplicate": True, "title": dup["title"]}
        pending = conn.execute("SELECT id FROM submissions WHERE status='pending' AND json_extract(payload,'$.url') = ?", (s.url.strip(),)).fetchone()
        if pending:
            return {"ok": True, "duplicate": True, "pending": True}
        cur = conn.execute(
            "INSERT INTO submissions (payload, submitted_by, note, created_at) VALUES (?,?,?,?)",
            (json.dumps(_payload(s)), (s.submitted_by or "").strip() or None, (s.note or "").strip() or None, _now()),
        )
    return {"ok": True, "id": cur.lastrowid}


@app.get("/api/admin/submissions", dependencies=[Depends(require_admin)])
def list_submissions(status: str = "pending"):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM submissions WHERE status = ? ORDER BY created_at DESC", (status,))
        return [_sub_row(r) for r in rows]


@app.post("/api/admin/submissions/{sid}/approve", dependencies=[Depends(require_admin)])
def approve(sid: int, edited: SubmissionIn | None = None):
    with connect() as conn:
        row = conn.execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone()
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
            "UPDATE submissions SET status='approved', payload=?, reviewed_at=? WHERE id=?",
            (json.dumps(payload), _now(), sid),
        )
        opp = conn.execute("SELECT id, f1_flag, eligibility_note FROM opportunities WHERE source='community' AND source_id=?", (str(sid),)).fetchone()
    return {"ok": True, "opportunity_id": opp["id"], "f1_flag": opp["f1_flag"], "eligibility_note": opp["eligibility_note"]}


class Reject(BaseModel):
    reason: str | None = None


@app.post("/api/admin/submissions/{sid}/reject", dependencies=[Depends(require_admin)])
def reject(sid: int, body: Reject | None = None):
    with connect() as conn:
        cur = conn.execute(
            "UPDATE submissions SET status='rejected', review_note=?, reviewed_at=? WHERE id=? AND status='pending'",
            ((body.reason if body else None), _now(), sid),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "no pending submission with that id")
    return {"ok": True}


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
