"""HackerEarth hackathons (the JSON feed behind their browser extension).

Small (usually 5-15 live events), all online, mostly company-sponsored with prizes.
"""
from datetime import datetime

from models import Opportunity
from .http import get_json

URL = "https://www.hackerearth.com/chrome-extension/events/"


def _day(s: str | None) -> str | None:
    """'2026-09-27 23:59:00+02:00' -> '2026-09-27' (already in the event's own timezone)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except ValueError:
        return s[:10]


def fetch() -> list[Opportunity]:
    out: list[Opportunity] = []
    for e in get_json(URL).get("response", []):
        if e.get("status") not in ("ONGOING", "UPCOMING"):
            continue
        slug = e["url"].rstrip("/").rsplit("/", 1)[-1]
        out.append(Opportunity(
            source="hackerearth",
            source_id=slug,
            url=e["url"],
            title=e["title"].strip(),
            category="hackathon",
            description=(e.get("description") or "").strip() or None,
            tags=[t for t in [e.get("challenge_type")] if t] + (["college"] if e.get("college") else []),
            image_url=e.get("thumbnail") or e.get("cover_image"),
            deadline=_day(e.get("end_tz")),
            deadline_label="Submissions due",
            starts_at=_day(e.get("start_tz")),
            ends_at=_day(e.get("end_tz")),
            location_mode="online",
            compensation_type="prize",          # HackerEarth challenges are prize-based; amount isn't in the feed
            compensation_note="Prizes (see page)",
        ))
    return out
