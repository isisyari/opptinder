"""Curated rows from seed/manual.csv (export your spreadsheet to this shape).

This is where the real value lives for fellowships, research, scholarships and
conferences: those don't have APIs, and eligibility needs a human to read the
rules page once. Rows here are marked verified=1 so scrapers never overwrite them.
"""
import csv
from pathlib import Path

from models import Opportunity

CSV_PATH = Path(__file__).parent.parent / "seed" / "manual.csv"

_BOOL = {"1", "true", "yes", "y"}


def _b(v: str | None) -> bool:
    return (v or "").strip().lower() in _BOOL


def _s(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def fetch() -> list[Opportunity]:
    if not CSV_PATH.exists():
        return []
    out: list[Opportunity] = []
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            if not _s(r.get("title")) or not _s(r.get("url")):
                continue
            out.append(Opportunity(
                source="manual",
                source_id=_s(r.get("id")) or r["url"].strip(),
                url=r["url"].strip(),
                title=r["title"].strip(),
                category=(_s(r.get("category")) or "other"),
                organizer=_s(r.get("organizer")),
                description=_s(r.get("description")),
                tags=[t.strip() for t in (r.get("tags") or "").split(";") if t.strip()],
                deadline=_s(r.get("deadline")),
                deadline_label=_s(r.get("deadline_label")) or "Apply by",
                starts_at=_s(r.get("starts_at")),
                ends_at=_s(r.get("ends_at")),
                location_mode=_s(r.get("location_mode")) or "unknown",
                country=_s(r.get("country")),
                city=_s(r.get("city")),
                compensation_type=_s(r.get("compensation_type")) or "none",
                compensation_note=_s(r.get("compensation_note")),
                requires_us_citizen=_b(r.get("requires_us_citizen")),
                requires_work_auth=_b(r.get("requires_work_auth")),
                open_to_intl=_s(r.get("open_to_intl")) or "unknown",
                eligibility_note=_s(r.get("eligibility_note")),
                verified=True,
            ))
    return out
