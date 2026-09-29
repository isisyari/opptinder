"""Devpost hackathons.

Uses the JSON endpoint Devpost's own frontend calls. It's undocumented, so
treat it as fragile: if it 403s, the User-Agent probably needs refreshing.
"""
import re
from datetime import datetime

from models import Opportunity
from .geo import parse_location
from .http import get_json

URL = "https://devpost.com/api/hackathons?status[]=open&page={page}"
MAX_PAGES = 20


def _parse_period(s: str | None) -> tuple[str | None, str | None]:
    """'Jul 31 - Oct 01, 2026' or 'Dec 15, 2026 - Jan 10, 2027' -> (start_iso, end_iso)."""
    if not s:
        return None, None
    parts = [p.strip() for p in s.split(" - ")]
    if len(parts) != 2:
        return None, None
    start_s, end_s = parts
    try:
        end = datetime.strptime(end_s, "%b %d, %Y").date()
    except ValueError:
        return None, None
    if not re.search(r"\d{4}", start_s):
        start_s = f"{start_s}, {end.year}"
    try:
        start = datetime.strptime(start_s, "%b %d, %Y").date()
    except ValueError:
        start = None
    return (start.isoformat() if start else None), end.isoformat()


def _prize_text(html: str | None) -> str | None:
    if not html:
        return None
    text = re.sub(r"<[^>]+>", "", html).strip()
    return f"{text} in prizes" if text else None


def fetch() -> list[Opportunity]:
    out: list[Opportunity] = []
    for page in range(1, MAX_PAGES + 1):
        data = get_json(URL.format(page=page))
        items = data.get("hackathons", [])
        if not items:
            break
        for h in items:
            loc = (h.get("displayed_location") or {}).get("location", "") or ""
            mode, country, city = parse_location(loc)
            start, end = _parse_period(h.get("submission_period_dates"))
            out.append(Opportunity(
                source="devpost",
                source_id=str(h["id"]),
                url=h["url"],
                title=h["title"],
                category="hackathon",
                organizer=h.get("organization_name"),
                tags=[t["name"] for t in h.get("themes", [])],
                image_url=("https:" + h["thumbnail_url"]) if h.get("thumbnail_url", "").startswith("//") else h.get("thumbnail_url"),
                deadline=end,
                deadline_label="Submissions due",
                starts_at=start,
                ends_at=end,
                location_mode=mode,
                country=country,
                city=city,
                compensation_type="prize" if (h.get("prizes_counts") or {}).get("cash") else "none",
                compensation_note=_prize_text(h.get("prize_amount")),
                open_to_intl="no" if h.get("invite_only") else "unknown",
                description=h.get("eligibility_requirement_invite_only_description"),
            ))
    return out
