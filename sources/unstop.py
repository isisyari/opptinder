"""Unstop hackathons + competitions (public JSON API; robots.txt allows /api/public/*).

The catalogue is India-heavy and mostly in person, so we keep only ONLINE events
plus anything physically in the US. Timestamps carry their own offset (+05:30),
so slicing the date is safe.
"""
from __future__ import annotations

from models import Opportunity
from .http import get_json

URL = ("https://unstop.com/api/public/opportunity/search-result"
       "?opportunity={kind}&per_page=50&oppstatus=open&page={page}")
KINDS = {"hackathons": "hackathon", "competitions": "competition"}
MAX_PAGES = 8
CURRENCY = {"fa-rupee": "₹", "fa-dollar": "$", "fa-usd": "$", "fa-euro": "€", "fa-gbp": "£"}


def _prize(prizes: list[dict]) -> tuple[str, str | None]:
    cash = [p for p in (prizes or []) if isinstance(p, dict) and p.get("cash")]
    if not cash:
        return ("prize", "Certificates / prizes") if prizes else ("none", None)
    total = sum(int(p["cash"]) for p in cash)
    sym = CURRENCY.get((cash[0].get("currency") or ""), "")
    return "prize", f"{sym}{total:,} in prizes"


def fetch() -> list[Opportunity]:
    out: list[Opportunity] = []
    for kind, category in KINDS.items():
        for page in range(1, MAX_PAGES + 1):
            data = get_json(URL.format(kind=kind, page=page)).get("data", {})
            items = data.get("data", [])
            if not items:
                break
            for it in items:
                region = it.get("region")
                country = ((it.get("address_with_country_logo") or {}).get("country") or {}).get("iso")
                online = region == "online"
                if not online and country not in ("US", "USA"):
                    continue
                reg = it.get("regnRequirements") or {}
                org = it.get("organisation") or {}
                comp, note = _prize(it.get("prizes") or [])
                # "competitions" on Unstop include hundreds of college quizzes and ideathons with no
                # award. Keep only ones with a cash prize; hackathons are kept regardless.
                if category == "competition" and not (note and note[0] in "₹$€£"):
                    continue
                tags = [f.get("name") for f in (it.get("filters") or []) if isinstance(f, dict) and f.get("name")]
                tags += [w.get("name") for w in (it.get("workfunction") or []) if isinstance(w, dict) and w.get("name")]
                if it.get("subtype"):
                    tags.append(str(it["subtype"]).replace("_", " "))
                if it.get("isPaid"):
                    tags.append("paid registration")
                logo = it.get("logoUrl2")
                out.append(Opportunity(
                    source="unstop",
                    source_id=str(it["id"]),
                    url=it.get("seo_url") or f"https://unstop.com/{it.get('public_url')}",
                    title=it["title"].strip(),
                    category=category,
                    organizer=org.get("name") if isinstance(org, dict) else None,
                    tags=tags[:8],
                    image_url=logo if isinstance(logo, str) and logo.startswith("http") else None,
                    deadline=(reg.get("end_regn_dt") or it.get("end_date") or "")[:10] or None,
                    deadline_label="Registration closes",
                    starts_at=(reg.get("start_regn_dt") or "")[:10] or None,
                    ends_at=(it.get("end_date") or "")[:10] or None,
                    location_mode="online" if online else "in_person",
                    country=None if online else "United States",
                    compensation_type=comp,
                    compensation_note=note,
                ))
            if page >= int(data.get("last_page") or 1):
                break
    return out
