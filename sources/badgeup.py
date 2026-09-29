"""BadgeUp: community-curated undergrad conferences (free / scholarship / student rate).

Source: https://github.com/Jose-Gael-Cruz-Lopez/BadgeUp
The README is the canonical list (web/lib/conferences.ts is only a sample), so we
parse the markdown tables. Columns:
  Status | Conference | Focus | Dates & Location | Access | Audience | Register/Apply | Deadline
Conferences repeat across the "by community" sections; we keep one row per
conference and collect every section it appears in as tags.
"""
from __future__ import annotations

import re
import urllib.request
from datetime import date

from models import Opportunity
from .geo import parse_location
from .http import UA, _ssl_context

README = "https://raw.githubusercontent.com/Jose-Gael-Cruz-Lopez/BadgeUp/main/README.md"

SECTIONS = {
    "Tech & Computing", "Business & Finance", "Engineering", "Health & Medicine",
    "Sciences & Research", "Social Sciences & Policy", "Law & Government",
    "Arts, Humanities & Design", "Education", "Multidisciplinary / General",
    "Women in Their Field", "Black Professionals & Students",
    "Latinx Professionals & Students", "LGBTQ+ in Their Field",
}
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}

# "Oct 28-29, 2026" | "Nov 30-Dec 4, 2026" | "Sep 16, 2026"
RANGE_RE = re.compile(r"\b([A-Z][a-z]{2}) (\d{1,2})(?:-(?:([A-Z][a-z]{2}) )?(\d{1,2}))?, (\d{4})")
# "May 2027" | "Oct-Nov 2026"
MONTH_RE = re.compile(r"\b([A-Z][a-z]{2})(?:-([A-Z][a-z]{2}))? (\d{4})")
# a date inside the free-text deadline cell: "Sep 21, 2026" or "Aug 25"
LOOSE_RE = re.compile(r"\b([A-Z][a-z]{2}) (\d{1,2})(?:, (\d{4}))?")


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as r:
        return r.read().decode()


def _iso(y: int, m: str, d: int) -> str | None:
    try:
        return date(y, MONTHS[m], d).isoformat()
    except (KeyError, ValueError):
        return None


def _event_dates(s: str) -> tuple[str | None, str | None]:
    m = RANGE_RE.search(s)
    if m:
        m1, d1, m2, d2, y = m.groups()
        start = _iso(int(y), m1, int(d1))
        end = _iso(int(y), m2 or m1, int(d2)) if d2 else start
        return start, end
    m = MONTH_RE.search(s)
    if m and m.group(1) in MONTHS:
        return _iso(int(m.group(3)), m.group(1), 1), None
    return None, None


def _deadline(text: str, event_start: str | None, event_end: str | None) -> tuple[str | None, str]:
    """-> (iso_date, label). Earliest upcoming date in the deadline cell that isn't after the event
    starts. Falls back to the event start if that's upcoming, else the event end (so past events expire)."""
    today = date.today().isoformat()
    ev_year = int(event_start[:4]) if event_start else int(today[:4])
    candidates = []
    for m, d, y in LOOSE_RE.findall(text):
        years = [int(y)] if y else [ev_year, ev_year - 1]      # no year given: try event year, then the year before
        for yy in years:
            iso = _iso(yy, m, int(d))
            if iso and iso >= today and (not event_start or iso <= event_start):
                candidates.append(iso)
                break
    if candidates:
        low = text.lower()
        label = ("Early bird ends" if "early" in low else
                 "Applications due" if any(w in low for w in ("application", "apply", "scholarship", "grant")) else
                 "Registration deadline")
        return min(candidates), label
    if event_start and event_start >= today:
        return event_start, "Event starts"
    return (event_end or event_start), "Event ends"


def parse(md: str) -> list[Opportunity]:
    by_id: dict[str, Opportunity] = {}
    section = None
    for line in md.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section not in SECTIONS or not line.startswith("| ") or line.startswith("| Status") or line.startswith("| ---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 8:
            continue
        status_c, name, focus, dates_loc, access, audience, link_c, deadline_c = cells[:8]
        url_m = re.search(r'href="([^"]+)"', link_c)
        if not url_m or not name:
            continue
        sid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if sid in by_id:                       # repeated in a community section: just add the tag
            if section not in by_id[sid].tags:
                by_id[sid].tags.append(section)
            continue

        status_m = re.search(r"\[(OPEN|CLOSING SOON|OPENS SOON)\]", status_c)
        status = status_m.group(1) if status_m else "OPEN"
        dates_part, _, loc_part = dates_loc.partition("·")
        start, end = _event_dates(dates_part)
        mode, country, city = parse_location(loc_part or dates_part)

        if "scholarship" in access.lower():
            comp, comp_note = "funding", "Scholarship / travel grant available"
        else:
            comp, comp_note = "none", access
        deadline, deadline_label = _deadline(deadline_c, start, end)

        by_id[sid] = Opportunity(
            source="badgeup",
            source_id=sid,
            url=url_m.group(1),
            title=name,
            category="conference",
            description=f"{dates_loc}. {deadline_c}. Audience: {audience}."
                        + (" (Not open yet for the next cycle.)" if status == "OPENS SOON" else ""),
            tags=[t.strip() for t in re.split(r"[,/]", focus) if t.strip()][:4] + [section],
            deadline=deadline,
            deadline_label=deadline_label,
            starts_at=start,
            ends_at=end,
            location_mode=mode,
            country=country,
            city=city,
            compensation_type=comp,
            compensation_note=comp_note,
        )
    return list(by_id.values())


def fetch() -> list[Opportunity]:
    return parse(_fetch_text(README))
