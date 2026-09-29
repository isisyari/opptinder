"""Turn free-text locations into (location_mode, country, city)."""
from __future__ import annotations

import re

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
US_NAMES = {"united states", "usa", "us", "u.s.", "u.s.a."}
VIRTUAL_RE = re.compile(r"\+?\s*\b(virtual|online|worldwide|remote|hybrid)\b", re.I)


def parse_location(s: str | None) -> tuple[str, str | None, str | None]:
    """'San Francisco, CA + Virtual' -> ('hybrid', 'United States', 'San Francisco')
       'Online' -> ('online', None, None);  'Toronto, Canada' -> ('in_person', 'Canada', 'Toronto')
       '' / 'Location TBA' -> ('unknown', None, None)"""
    s = (s or "").strip()
    virtual = bool(VIRTUAL_RE.search(s))
    physical = VIRTUAL_RE.sub("", s).strip(" +·,")
    if not physical or "tba" in physical.lower():
        return ("online" if virtual else "unknown"), None, None
    mode = "hybrid" if virtual else "in_person"
    parts = [p.strip() for p in physical.split(",") if p.strip()]
    tail_tokens = set(re.findall(r"[A-Z]{2}", parts[-1])) if parts else set()
    if len(parts) >= 2 and (tail_tokens & US_STATES or parts[-1].lower() in US_NAMES):
        return mode, "United States", parts[0]
    if len(parts) == 1 and parts[0].lower() in US_NAMES:
        return mode, "United States", None
    if len(parts) >= 2:
        return mode, parts[-1], parts[0]
    return mode, None, physical


def is_us_or_remote(mode: str, country: str | None) -> bool:
    return mode in ("online", "hybrid") or (country or "").lower() in US_NAMES | {"united states"}
