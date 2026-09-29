"""'Academic Research Opportunities for International Students' (GitHub, smsnobin77).

README tables of research / mentorship programs, grouped by region, each with a
deadline and a comment that usually states international eligibility and pay.
Many rows carry last cycle's date; those expire and come back when the list updates.
"""
from __future__ import annotations

import re
import urllib.request
from datetime import datetime

from models import Opportunity
from .http import UA, _ssl_context

README = "https://raw.githubusercontent.com/smsnobin77/Graduate-global-research-mentorship-opportunities/main/README.md"
SOURCE_PAGE = "https://github.com/smsnobin77/Graduate-global-research-mentorship-opportunities"

LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
DATE_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})")
REGION_COUNTRY = {"north america": "United States", "europe": None, "asia": None, "oceania": None, "africa": None, "south america": None}


def _deadline(s: str) -> str | None:
    m = DATE_RE.search(s)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
    except ValueError:
        return None


def parse(md: str) -> list[Opportunity]:
    out: list[Opportunity] = []
    region = None
    for line in md.splitlines():
        if line.startswith("### "):
            region = line[4:].strip()
            continue
        if not line.startswith("| ") or line.startswith("| Program") or line.startswith("| ---") or line.startswith("|--"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        name_c, deadline_c, comment = cells[0], cells[1], cells[2]
        link = LINK_RE.search(name_c)
        if not link:
            continue
        url = link.group(2)
        title = link.group(1) if link.group(1).lower() != "link" else name_c.split("[")[0].strip()
        low = comment.lower()
        online = "remote" in low or "online" in low
        country = REGION_COUNTRY.get((region or "").lower())
        if "canada" in low or "canada" in title.lower():
            country = "Canada"
        paid = any(w in low for w in ("stipend", "paid", "funding", "salary", "$"))
        unpaid = "unpaid" in low or "no cost" in low
        comp = "none" if (unpaid or not paid) else "stipend"
        intl = any(w in low for w in ("global", "international", "worldwide"))
        grad = bool(re.search(r"\b(graduate|phd|predoc|pre-doc|predoctoral|pre-doctoral|master'?s)\b", low)) and "undergraduate" not in low
        cat = "fellowship" if "fellowship" in title.lower() else "research"
        tags = ["research", region or "international"] + (["graduate level"] if grad else []) + (["remote"] if online else [])
        out.append(Opportunity(
            source="mentorship",
            source_id=re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
            url=url,
            title=title,
            category=cat,
            description=f"{comment} Deadline listed: {deadline_c}.",
            tags=tags,
            deadline=_deadline(deadline_c),
            deadline_label="Applications due",
            location_mode="online" if online else ("in_person" if country else "unknown"),
            country=None if online else country,
            compensation_type=comp,
            compensation_note=("Stipend / funded" if comp == "stipend" else ("Unpaid" if unpaid else None)),
            open_to_intl="yes" if intl else "unknown",
            eligibility_note=(f"Curated for international students. Listing says: “{comment[:160]}”"),
        ))
    return out


def fetch() -> list[Opportunity]:
    req = urllib.request.Request(README, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as r:
        return parse(r.read().decode())
