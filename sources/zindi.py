"""Zindi data-science competitions (undocumented but stable JSON at api.zindi.world).

All online. Some competitions are restricted to particular groups (often Africa-based
participants); the API only exposes an `is_access_restricted` flag, so those get
flagged for a rules check rather than dropped.
"""
from __future__ import annotations

import re

from models import Opportunity
from .http import get_json

URL = "https://api.zindi.world/v1/competitions"


def fetch() -> list[Opportunity]:
    out: list[Opportunity] = []
    for c in get_json(URL).get("data", []):
        if not c.get("open"):
            continue
        reward = (c.get("reward") or "").strip()
        has_cash = c.get("reward_type") == "prize" and re.search(r"\d", reward)
        restricted = bool(c.get("is_access_restricted"))
        tags = ["data science", "machine learning", c.get("kind") or "competition"]
        if c.get("is_beginner_friendly"):
            tags.append("Beginner friendly")
        org = c.get("organization")
        out.append(Opportunity(
            source="zindi",
            source_id=str(c["id"]),
            url=f"https://zindi.africa/competitions/{c['id']}",
            title=c["title"].strip(),
            category="competition",
            organizer=org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else None),
            tags=tags,
            image_url=c.get("image"),
            deadline=(c.get("end_time") or "")[:10] or None,
            deadline_label="Submissions due",
            starts_at=(c.get("start_time") or "")[:10] or None,
            ends_at=(c.get("end_time") or "")[:10] or None,
            location_mode="online",
            compensation_type="prize" if has_cash else "none",
            compensation_note=(f"{reward} in prizes" if has_cash else (reward or "Knowledge / leaderboard points")),
            open_to_intl="unknown" if restricted else "yes",
            eligibility_note=("Entry is restricted to a specific group (often Africa-based participants). Read the rules before entering."
                              if restricted else None),
        ))
    return out
