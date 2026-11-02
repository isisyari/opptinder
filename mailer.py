"""Send transactional email, or log it when no provider is configured.

Two providers, picked by which key is set (Brevo wins if both are):

    BREVO_API_KEY    https://app.brevo.com → SMTP & API → API keys. Free 300/day. Can send from a
                     verified personal address (e.g. your Gmail) with no domain needed.
    BREVO_FROM       the sender you verified in Brevo → Senders, e.g. "F-1 Opportunities <you@gmail.com>"

    RESEND_API_KEY   https://resend.com. Free ~100/day, but without a verified domain it only delivers
                     to the account owner's own address.
    RESEND_FROM      e.g. "F-1 Opportunities <hello@yourdomain.org>" (domain verified in Resend)

Unset both -> the message is printed to the server log (local development / Vercel Logs).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from sources.http import _ssl_context


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def configured() -> bool:
    return bool(_env("BREVO_API_KEY") or _env("RESEND_API_KEY"))


def _split_from(s: str, default_name: str) -> tuple[str, str]:
    """'Name <addr>' -> (Name, addr); bare 'addr' -> (default_name, addr)."""
    m = re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", s)
    return (m.group(1) or default_name, m.group(2)) if m else (default_name, s.strip())


def _post(url: str, headers: dict, body: dict) -> bool:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        print(f"[mail] provider returned {e.code}: {e.read()[:300]!r}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[mail] send failed: {e}")
    return False


def send(to: str, subject: str, text: str) -> bool:
    """Returns True if a provider accepted the message. Without a provider the message is logged and
    False is returned so the caller can tell the user to check the server log."""
    if _env("BREVO_API_KEY"):
        name, addr = _split_from(_env("BREVO_FROM"), "F-1 Opportunities")
        if not addr:
            print("[mail] BREVO_FROM is not set (must be a sender verified in Brevo)")
            return False
        return _post("https://api.brevo.com/v3/smtp/email", {"api-key": _env("BREVO_API_KEY")},
                     {"sender": {"name": name, "email": addr}, "to": [{"email": to}],
                      "subject": subject, "textContent": text})
    if _env("RESEND_API_KEY"):
        sender = _env("RESEND_FROM") or "F-1 Opportunities <onboarding@resend.dev>"
        return _post("https://api.resend.com/emails", {"Authorization": f"Bearer {_env('RESEND_API_KEY')}"},
                     {"from": sender, "to": [to], "subject": subject, "text": text})
    print(f"[mail] (no email provider configured) to={to} subject={subject!r}\n{text}")
    return False
