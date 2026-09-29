"""Hack Club Hackathons (documented public API, https://hackathons.hackclub.com/data).

Attribution required: "Hack Club Hackathons" with a link. The list is mostly
high-school hackathons, many open to anyone; we tag them so the card says so.
"""
from __future__ import annotations

from models import Opportunity
from .http import get_json

URL = "https://hackathons.hackclub.com/api/events/upcoming/"
COUNTRY_NAMES = {"US": "United States", "USA": "United States", "CA": "Canada", "GB": "United Kingdom", "IN": "India"}


def fetch() -> list[Opportunity]:
    out: list[Opportunity] = []
    for e in get_json(URL):
        virtual, hybrid = bool(e.get("virtual")), bool(e.get("hybrid"))
        mode = "online" if virtual and not hybrid else ("hybrid" if hybrid else "in_person")
        code = e.get("countryCode") or ""
        country = COUNTRY_NAMES.get(code, e.get("country")) if mode != "online" else None
        tags = ["high school", "Hack Club"] + (["MLH"] if e.get("mlhAssociated") else [])
        out.append(Opportunity(
            source="hackclub",
            source_id=str(e["id"]),
            url=e.get("website") or f"https://hackathons.hackclub.com/{e['id']}",
            title=e["name"].strip(),
            category="hackathon",
            organizer="Hack Club Hackathons (listing)",
            description="Listed by Hack Club Hackathons, a directory of mostly high-school hackathons. Check the event's own age and enrollment rules.",
            tags=tags,
            image_url=e.get("banner") or e.get("logo"),
            deadline=(e.get("start") or "")[:10] or None,
            deadline_label="Event starts",
            starts_at=(e.get("start") or "")[:10] or None,
            ends_at=(e.get("end") or "")[:10] or None,
            location_mode=mode,
            country=country,
            city=None if mode == "online" else e.get("city"),
            compensation_type="none",
            eligibility_note="Hackathon participation is not employment. Many Hack Club events are for high-school students; confirm you're eligible to enter.",
        ))
    return out
