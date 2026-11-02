"""Send transactional email through Resend (https://resend.com), or log it when no key is set.

Environment:
    RESEND_API_KEY   from the Resend dashboard. Unset -> emails are printed to the server log instead,
                     which is how local development works.
    RESEND_FROM      sender, e.g. "F-1 Opportunities <hello@yourdomain.org>". The domain must be verified
                     in Resend. Default is Resend's test sender, which only delivers to the address that
                     owns the Resend account.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from sources.http import _ssl_context

API = "https://api.resend.com/emails"
FROM = os.environ.get("RESEND_FROM", "F-1 Opportunities <onboarding@resend.dev>")


def configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY", "").strip())


def send(to: str, subject: str, text: str) -> bool:
    """Returns True if the provider accepted the message. Without RESEND_API_KEY the message is
    logged and False is returned so the caller can tell the user to check the server log."""
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if not key:
        print(f"[mail] (no RESEND_API_KEY) to={to} subject={subject!r}\n{text}")
        return False
    body = json.dumps({"from": FROM, "to": [to], "subject": subject, "text": text}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        print(f"[mail] resend returned {e.code}: {e.read()[:300]!r}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[mail] send failed: {e}")
    return False
