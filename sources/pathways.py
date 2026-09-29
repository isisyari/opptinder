"""Pathways to Science (Institute for Broadening Participation): funded research programs
that explicitly accept non-U.S. citizens.

One server-rendered page lists every match, so a run is a single request, same as a
visitor using the search form. It's a nonprofit with no published reuse policy, so this
source is OPT-IN: set PATHWAYS_OK=1 in the environment to run it (see docs/outreach.md
for the permission email). Deadlines live on detail pages; we don't crawl those.
"""
from __future__ import annotations

import html as htmllib
import os
import re
import urllib.request

from models import Opportunity
from .geo import parse_location
from .http import UA, _ssl_context

URL = ("https://www.pathwaystoscience.org/programs.aspx"
       "?u=Undergrads_Undergraduate%20Students&c=NonUSCitizen&submit=y&adv=adv")
BASE = "https://www.pathwaystoscience.org/"

INST_RE = re.compile(r"<div class='progigert' style='background: #dedede[^>]*>\s*<h2[^>]*>(.*?)</h2>\s*<i><span[^>]*>\s*\((.*?)\)\s*</span></i>", re.S)
PROG_RE = re.compile(r"<div class='progigert' style='background: #efefef[^>]*>(.*?)<div style='clear:both'", re.S)
LINK_RE = re.compile(r"<a href='programhub\.aspx\?sort=([^']+)'>(.*?)</a>", re.S)
DESC_RE = re.compile(r"<div style='padding-left:0px[^>]*>(.*?)<a href='programhub", re.S)
IMG_RE = re.compile(r"<img src='([^']+)'[^>]*alt='([^']*)'", re.S)


def _text(s: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse(page: str) -> list[Opportunity]:
    """Programs run by consortia appear under each partner institution; keep the first listing."""
    out: list[Opportunity] = []
    seen: set[str] = set()
    # walk the page in order: each institution header is followed by its program blocks
    tokens = re.split(r"(?=<div class='progigert' style='background: #dedede)", page)
    for chunk in tokens:
        inst = INST_RE.search(chunk)
        if not inst:
            continue
        institution, place = _text(inst.group(1)), _text(inst.group(2))
        mode, country, city = parse_location(place)
        if country is None and re.search(r",\s*[A-Z]{2}$", place):
            country = "United States"
        for block in PROG_RE.findall(chunk):
            link = LINK_RE.search(block)
            if not link:
                continue
            pid, title = link.group(1), _text(link.group(2))
            if pid in seen:
                continue
            seen.add(pid)
            desc_m = DESC_RE.search(block)
            desc = _text(desc_m.group(1)) if desc_m else None
            imgs = IMG_RE.findall(block)
            alts = " ".join(a.lower() for _, a in imgs)
            funders = [a for _, a in imgs if a and "international" not in a.lower()]
            thumb = next((src for src, a in imgs if a == "" and pid in src), None)
            intl = "funds international students" in alts
            tags = ["summer research", "STEM"] + [f for f in funders if f]
            if any("nsf" in f.lower() for f in funders):
                tags.append("NSF-funded")
            out.append(Opportunity(
                source="pathways",
                source_id=pid,
                url=f"{BASE}programhub.aspx?sort={pid}",
                title=title,
                category="research",
                organizer=institution,
                description=(desc or "") + " Deadline and stipend details are on the program page.",
                tags=tags,
                image_url=thumb,
                deadline=None,
                deadline_label="See program page",
                location_mode=mode if mode != "unknown" else "in_person",
                country=country,
                city=city,
                compensation_type="stipend",
                compensation_note="Funded program (stipend/housing vary)",
                open_to_intl="yes" if intl else "unknown",
                eligibility_note=("Pathways to Science lists this program as accepting and funding non-U.S. citizens. "
                                  "Summer research stipends are usually fine on F-1 but the mechanism varies by school: confirm with your DSO."
                                  if intl else
                                  "Matched Pathways to Science's non-U.S.-citizen search; funding for international students not confirmed. Read the program page."),
            ))
    return out


def fetch() -> list[Opportunity]:
    if os.environ.get("PATHWAYS_OK") != "1":
        print("[pathways] opt-in source: set PATHWAYS_OK=1 after confirming reuse permission (docs/outreach.md). Skipping.")
        return []
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40, context=_ssl_context()) as r:
        return parse(r.read().decode("utf-8", "replace"))
