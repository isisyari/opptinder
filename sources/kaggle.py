"""Kaggle competitions via the official public API (needs a free token).

Setup (one time): kaggle.com -> your avatar -> Settings -> API -> "Create New Token".
Kaggle now gives you a single access token (starts with KGAT_). Either:
    export KAGGLE_API_TOKEN=KGAT_...
or  mkdir -p ~/.kaggle && echo KGAT_... > ~/.kaggle/access_token && chmod 600 ~/.kaggle/access_token
The older kaggle.json (username + key) still works too.
Without credentials this source is skipped with a message instead of failing.
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from models import Opportunity
from .http import get_json

URL = "https://www.kaggle.com/api/v1/competitions/list?group=general&sortBy=latestDeadline&page={page}"
MAX_PAGES = 5
SKIP_CATEGORIES = {"Getting Started"}     # evergreen tutorials, not resume material


def _auth_header() -> str | None:
    """Bearer token (new KGAT_ style) or Basic auth from the legacy kaggle.json."""
    cfg = Path(os.environ.get("KAGGLE_CONFIG_DIR", Path.home() / ".kaggle"))
    token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if not token and (cfg / "access_token").exists():
        token = (cfg / "access_token").read_text().strip()
    if token:
        return f"Bearer {token}"
    u, k = os.environ.get("KAGGLE_USERNAME"), os.environ.get("KAGGLE_KEY")
    if not (u and k) and (cfg / "kaggle.json").exists():
        d = json.loads((cfg / "kaggle.json").read_text())
        u, k = d.get("username"), d.get("key")
    if u and k:
        return "Basic " + base64.b64encode(f"{u}:{k}".encode()).decode()
    return None


def _reward(s: str | None) -> tuple[str, str | None]:
    s = (s or "").strip()
    m = re.match(r"^\$?([\d,]+)\s*(usd)?$", s, re.I)          # "60,000 Usd" / "$60,000"
    if m:
        return "prize", f"${m.group(1)} in prizes"
    if re.search(r"\d", s) and ("$" in s or "€" in s or "usd" in s.lower()):
        return "prize", f"{s} in prizes"
    if s and s.lower() not in ("knowledge", "kudos"):
        return "none", s          # "Swag", "Medals", etc.
    return "none", "Leaderboard medals / knowledge"


def fetch() -> list[Opportunity]:
    auth = _auth_header()
    if not auth:
        print("[kaggle] no credentials; set KAGGLE_API_TOKEN or save it to ~/.kaggle/access_token. Skipping.")
        return []

    out: list[Opportunity] = []
    for page in range(1, MAX_PAGES + 1):
        items = get_json(URL.format(page=page), headers={"Authorization": auth})
        if not items:
            break
        for c in items:
            cat = c.get("category") or ""
            if cat in SKIP_CATEGORIES:
                continue
            comp, note = _reward(c.get("reward"))
            elig = None
            if cat == "Recruitment":
                elig = ("Competing is fine on F-1. Any job offer that follows would need CPT/OPT, "
                        "so treat it as a portfolio piece, not a hiring pipeline.")
            out.append(Opportunity(
                source="kaggle",
                source_id=str(c.get("ref") or c.get("id")),
                url=c.get("url") or f"https://www.kaggle.com/competitions/{c.get('ref')}",
                title=c.get("title") or str(c.get("ref")),
                category="competition",
                organizer=c.get("organizationName"),
                description=c.get("description"),
                image_url=c.get("thumbnailImageUrl") or None,
                tags=[t.get("name") for t in (c.get("tags") or []) if isinstance(t, dict) and t.get("name")] + ([cat] if cat else []),
                deadline=(c.get("deadline") or "")[:10] or None,
                deadline_label="Submissions due",
                starts_at=(c.get("enabledDate") or "")[:10] or None,
                ends_at=(c.get("deadline") or "")[:10] or None,
                location_mode="online",
                compensation_type=comp,
                compensation_note=note,
                open_to_intl="unknown",
                eligibility_note=elig,
            ))
    return out
