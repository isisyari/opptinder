"""Devfolio hackathons (public search endpoint used by devfolio.co/hackathons).

Mostly India-based in-person events plus a good number of online ones.
The feed's default filter (online OR United States) handles the noise.

Timestamps come back in UTC; an event at midnight IST is 18:30 UTC the day
before, so we convert to the event's own timezone before taking the date.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from models import Opportunity
from .http import get_json

URL = "https://api.devfolio.co/api/search/hackathons"
PAGE = 50


def _day(iso: str | None, tz: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if tz:
            dt = dt.astimezone(ZoneInfo(tz))
        return dt.date().isoformat()
    except Exception:
        return iso[:10]


def fetch() -> list[Opportunity]:
    out: list[Opportunity] = []
    offset = 0
    while True:
        data = get_json(URL, method="POST", body={"type": "application_open", "from": offset, "size": PAGE})
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        for hit in hits:
            s = hit["_source"]
            setting = s.get("hackathon_setting") or {}
            tz = s.get("timezone")
            online = bool(s.get("is_online"))
            prizes = s.get("prizes") or []
            reg_ends = _day(setting.get("reg_ends_at"), tz)
            ends = _day(s.get("ends_at"), tz)
            out.append(Opportunity(
                source="devfolio",
                source_id=s["uuid"],
                url=f"https://{s['slug']}.devfolio.co",
                title=s["name"],
                category="hackathon",
                organizer=(s.get("hosted_by") or {}).get("name") if isinstance(s.get("hosted_by"), dict) else None,
                description=s.get("tagline"),
                tags=[t.get("name") for t in (s.get("themes") or []) if isinstance(t, dict) and t.get("name")]
                     + [h for h in (s.get("hashtags") or []) if isinstance(h, str)],
                image_url=s.get("cover_img"),
                deadline=reg_ends or ends,
                deadline_label="Registration closes" if reg_ends else "Event ends",
                starts_at=_day(s.get("starts_at"), tz),
                ends_at=ends,
                location_mode="online" if online else "in_person",
                country=None if online else s.get("country"),
                city=None if online else s.get("city"),
                compensation_type="prize" if prizes else "none",
                compensation_note=f"{len(prizes)} prize tracks" if prizes else None,
            ))
        offset += PAGE
        if offset >= data.get("hits", {}).get("total", {}).get("value", 0):
            break
    return out
