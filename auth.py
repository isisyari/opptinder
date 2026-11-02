"""Passwordless sign-in: name + email.

    POST /api/auth/request  {email, name, anon_user}  simple mode: signs in, sets the cookie, merges swipes
                                                      code mode:   emails a 6-digit code (10 min, 5 attempts)
    POST /api/auth/verify   {email, code, anon_user}  code mode only: checks the code, then signs in
    GET  /api/auth/me                                 -> {user: {name, email} | null, mode: simple | code}
    POST /api/auth/logout

Sessions are a random token in an HttpOnly cookie; only its sha256 is stored. Signed-in users' swipes
live under user_id 'u:<id>' (see `current_user_id`), so every feed/swipe/saved route works the same
for anonymous and signed-in visitors.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

import mailer
from models import connect

router = APIRouter(prefix="/api/auth")

COOKIE = "session"
# Default: name + a well-formed email is enough (no email provider to set up; anyone who types your
# address could see your saved list, which is the accepted trade-off for this app). Set
# REQUIRE_EMAIL_CODE=1 plus BREVO_* / RESEND_* to prove addresses with a 6-digit emailed code.
REQUIRE_CODE = os.environ.get("REQUIRE_EMAIL_CODE", "").strip() == "1"
SESSION_DAYS = 180
CODE_MINUTES = 10
CODE_ATTEMPTS = 5
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
APP_NAME = "F-1 Opportunities"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------- rate limiting (in-memory, per process)

_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def client_ip(request: Request) -> str:
    """Behind Fly/Railway/Caddy the socket peer is the proxy; the real address is the first X-Forwarded-For entry."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(key: str, limit: int, window: int, message: str = "Too many requests; try again later.") -> None:
    now = time.monotonic()
    with _hits_lock:
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, message)
        q.append(now)
        if len(_hits) > 10_000:                 # don't let a scan grow the table forever
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
                del _hits[k]


# ---------------------------------------------------------------- sessions

def user_from_request(request: Request):
    """The signed-in user row, or None. Touches users.last_seen at most once a day."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT u.*, s.expires_at AS session_expires FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = %s""", (_sha(token),)).fetchone()
        if not row:
            return None
        if row["session_expires"] < _iso(_now()):
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_sha(token),))
            return None
        if row["last_seen"][:10] != _iso(_now())[:10]:
            conn.execute("UPDATE users SET last_seen = %s WHERE id = %s", (_iso(_now()), row["id"]))
        return row


def current_user_id(request: Request, user: str = "local") -> str:
    """Swipe namespace for this request: the account if signed in, else the browser's anonymous id."""
    u = user_from_request(request)
    return f"u:{u['id']}" if u else user


def _set_cookie(response: Response, request: Request, token: str) -> None:
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=https, path="/")


# ---------------------------------------------------------------- routes

class VerifyCode(BaseModel):
    email: str
    code: str
    anon_user: str | None = None


def _clean_email(s: str) -> str:
    e = (s or "").strip().lower()
    if not EMAIL_RE.match(e):
        raise HTTPException(400, "That doesn't look like an email address.")
    return e


def _sign_in(conn, email: str, name: str, anon_user: str | None, now: datetime) -> tuple[dict, int, str]:
    """Find or create the user, merge the browser's anonymous swipes, open a session.
    -> ({name, email}, merged_count, session_token)"""
    user = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    if user:
        conn.execute("UPDATE users SET last_seen = %s WHERE id = %s", (_iso(now), user["id"]))
        uid = user["id"]
    else:
        uid = conn.execute(
            "INSERT INTO users (email, name, domain, created_at, last_seen) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (email, name, email.rsplit("@", 1)[1], _iso(now), _iso(now))).fetchone()["id"]
    # Bring this browser's anonymous swipes into the account. Where both exist, the account's wins.
    merged = 0
    if anon_user and not anon_user.startswith("u:"):
        merged = conn.execute(
            "INSERT INTO swipes (user_id, opportunity_id, action, created_at) "
            "SELECT %s, opportunity_id, action, created_at FROM swipes WHERE user_id = %s "
            "ON CONFLICT (user_id, opportunity_id) DO NOTHING",
            (f"u:{uid}", anon_user)).rowcount
        conn.execute("DELETE FROM swipes WHERE user_id = %s", (anon_user,))
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (%s,%s,%s,%s)",
                 (_sha(token), uid, _iso(now), _iso(now + timedelta(days=SESSION_DAYS))))
    conn.execute("DELETE FROM sessions WHERE expires_at < %s", (_iso(now),))
    return {"name": user["name"] if user else name, "email": email}, merged, token


class RequestCode(BaseModel):
    email: str
    name: str
    anon_user: str | None = None


@router.post("/request")
def request_code(body: RequestCode, request: Request, response: Response):
    """Simple mode (default): name + a well-formed email signs you straight in.
    Code mode (REQUIRE_EMAIL_CODE=1): emails a 6-digit code to prove the address."""
    email = _clean_email(body.email)
    name = re.sub(r"\s+", " ", body.name or "").strip()
    if not 1 <= len(name) <= 80:
        raise HTTPException(400, "Please enter your name (up to 80 characters).")
    ip = client_ip(request)
    rate_limit(f"auth-ip:{ip}", 20, 3600, "Too many sign-in attempts from this network; try again in an hour.")
    if not REQUIRE_CODE:
        with connect() as conn:
            user, merged, token = _sign_in(conn, email, name, body.anon_user, _now())
        _set_cookie(response, request, token)
        return {"ok": True, "user": user, "merged": merged}
    rate_limit(f"auth-email:{email}", 3, 900, "We already sent codes to that address; wait 15 minutes.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    with connect() as conn:
        conn.execute("UPDATE login_codes SET consumed = 1 WHERE email = %s AND consumed = 0", (email,))
        conn.execute(
            "INSERT INTO login_codes (email, name, code_hash, created_at, expires_at) VALUES (%s,%s,%s,%s,%s)",
            (email, name, _sha(code), _iso(now), _iso(now + timedelta(minutes=CODE_MINUTES))))
        conn.execute("DELETE FROM login_codes WHERE expires_at < %s", (_iso(now - timedelta(days=1)),))
    delivered = mailer.send(
        email, f"{code} is your {APP_NAME} code",
        f"Hi {name},\n\nYour sign-in code is {code}\n\nIt expires in {CODE_MINUTES} minutes. "
        f"If you didn't ask for this, you can ignore this email.\n\n{APP_NAME}")
    return {"ok": True, "delivered": delivered, "expires_in": CODE_MINUTES * 60}


@router.post("/verify")
def verify_code(body: VerifyCode, request: Request, response: Response):
    email = _clean_email(body.email)
    code = re.sub(r"\D", "", body.code or "")
    if len(code) != 6:
        raise HTTPException(400, "Enter the 6-digit code from the email.")
    rate_limit(f"verify-ip:{client_ip(request)}", 30, 3600)
    now = _now()
    with connect() as conn:
        rec = conn.execute(
            "SELECT * FROM login_codes WHERE email = %s AND consumed = 0 ORDER BY created_at DESC LIMIT 1",
            (email,)).fetchone()
        if not rec or rec["expires_at"] < _iso(now):
            raise HTTPException(400, "That code has expired. Request a new one.")
        # Raising inside `with connect()` rolls the transaction back, so commit the bookkeeping
        # writes first or the attempt counter would never move.
        if rec["attempts"] >= CODE_ATTEMPTS:
            conn.execute("UPDATE login_codes SET consumed = 1 WHERE id = %s", (rec["id"],))
            conn.commit()
            raise HTTPException(400, "Too many wrong attempts. Request a new code.")
        if not secrets.compare_digest(rec["code_hash"], _sha(code)):
            conn.execute("UPDATE login_codes SET attempts = attempts + 1 WHERE id = %s", (rec["id"],))
            conn.commit()
            left = CODE_ATTEMPTS - rec["attempts"] - 1
            raise HTTPException(400, f"Wrong code. {left} attempt{'s' if left != 1 else ''} left." if left else
                                "Wrong code. Request a new one.")
        conn.execute("UPDATE login_codes SET consumed = 1 WHERE id = %s", (rec["id"],))
        user, merged, token = _sign_in(conn, email, rec["name"], body.anon_user, now)
    _set_cookie(response, request, token)
    return {"ok": True, "user": user, "merged": merged}


@router.get("/me")
def me(request: Request):
    u = user_from_request(request)
    return {"user": {"name": u["name"], "email": u["email"]} if u else None,
            "mode": "code" if REQUIRE_CODE else "simple"}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_sha(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
