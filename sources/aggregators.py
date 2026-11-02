"""Opportunity aggregator blogs (WordPress) -> fellowships, scholarships, competitions, conferences.

These sites post single-opportunity articles daily, mostly open to students worldwide. They
all run WordPress, so we read the public REST API (`/wp-json/wp/v2/posts`) instead of HTML:
title, link, excerpt, categories and the article body, from which we pull the "Deadline:" line.

We store only the title, deadline, a short excerpt and a link back to the post (which carries the
official apply link). Each row's `source` is the site name so the card says "via <site>".

Sites and why:
  opportunitydesk.org        biggest; tags posts by eligible region, so posts not tagged "America" are skipped
  oyaop.com                  scholarships/fellowships, competitions, conferences, entrepreneurship events
  opportunitiescorners.com   cleanest deadlines; conferences/forums + fellowships

Skipped on purpose: internships, traineeships, jobs and volunteering (employment, needs CPT/OPT or is
abroad), PhD/postdoc/master's degree funding (not relevant to a student already enrolled on F-1),
high-school programs, and titles restricted to one nationality or region.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from models import Opportunity
from .http import UA, _ssl_context

LOOKBACK_DAYS = 90        # posts older than this have almost always closed
PER_PAGE = 100
MAX_PAGES = 6

# category id -> our category (fallback when the title has no clearer keyword). None = skip the post.
SITES: dict[str, dict] = {
    "opportunitydesk": {
        "base": "https://opportunitydesk.org",
        "label": "Opportunity Desk",
        "categories": {24: "fellowship", 11: "competition", 29: "competition", 6: "scholarship",
                       30: "fellowship", 10: "conference"},
        "skip_categories": {12, 161, 8, 105, 1282, 48236, 1281},   # jobs, internships, blog, volunteering, phd, postdoc, masters
        # OD tags every post with the regions whose residents may apply. No "America" tag = not for us.
        "region_categories": {21738, 21742, 21739, 21740, 21743},
        "required_region": 21742,
    },
    "oyaop": {
        "base": "https://www.oyaop.com",
        "label": "OYA Opportunities",
        "categories": {5: "scholarship", 7: "competition", 3: "conference", 6: "other", 8: "accelerator"},
        "skip_categories": {2, 14},                                 # internships, high school
    },
    "opportunitiescorners": {
        "base": "https://www.opportunitiescorners.com",
        "label": "Opportunities Corners",
        "categories": {66: "conference", 905: "fellowship", 61: "other", 1057: "competition",
                       936: "scholarship", 64: "scholarship"},
        "skip_categories": {31, 1058, 909, 1, 943},                 # internships, trainings, blog, online courses, visa
    },
}

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
# "December 10, 2026" | "Dec. 10 2026"
MDY_RE = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", re.I)
# "14th December 2026" | "7 September, 2026"
DMY_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON},?\s+(\d{{4}})", re.I)
# "Deadline: Dec 10, 2026" on one line, or an "Application Deadline" heading with the date a line or two below
DEADLINE_RE = re.compile(r"deadline[^A-Za-z0-9]{0,12}([\s\S]{0,160})", re.I)
ROLLING_RE = re.compile(r"\b(rolling|ongoing|not specified|varies|various|own deadline|n/a)\b", re.I)

TITLE_CATEGORY = [
    ("hackathon", re.compile(r"\bhackathon\b", re.I)),
    ("accelerator", re.compile(r"\b(accelerator|incubator|bootcamp for founders)\b", re.I)),
    ("conference", re.compile(r"\b(conference|summit|forum|congress|symposium|assembly|townhall|town hall|convention)\b", re.I)),
    ("fellowship", re.compile(r"\bfellows?(hip)?\b", re.I)),
    ("scholarship", re.compile(r"\bscholarships?\b", re.I)),
    ("competition", re.compile(r"\b(competition|contest|challenge|award|prize|olympiad|essay|pitch)\b", re.I)),
    ("research", re.compile(r"\b(research program|research programme|summer research|research school)\b", re.I)),
]
SKIP_TITLE_RE = re.compile(
    r"\b(internship|internships|intern|traineeship|trainee|jobs?|vacanc|recruit|hiring|volunteer|"
    r"phd|ph\.d|doctoral|postdoc|post-doc|master'?s|masters|mba|bachelor'?s degree|"
    r"high school|secondary school|currently open|opportunities for|roundup|round-up|"
    r"how to|tips|guide to|everything you need|what is|"
    # professional / academic-career fellowships are jobs, not student opportunities
    r"faculty|professors?|graduate research fellowship|mid-career|senior|executive|visiting (fellow|scholar)\w*|"
    r"for professionals|for journalists|for teachers|for lawyers|for doctors|for nurses|for artists|"
    r"for scientists|for researchers|for entrepreneurs|for founders|for policymakers|"
    r"journalis\w* (fellowship|grant|award)|reporting (fellowship|grant)|media fellowship|"
    # money for organisations, not people
    r"call for proposals|venture funding|funding opportunity|grants? for (organi[sz]ations|ngos|nonprofits|startups)|"
    # blog posts that slipped into an opportunity category
    r"what to do|after winning|life-changing)\b", re.I)
# a scholarship post that is really "come do a degree at university X" (irrelevant to a student already on F-1)
DEGREE_RE = re.compile(r"\b(university|college|institute of technology|mext|erasmus|daad|chevening|degree|tuition|study in|"
                       r"undergraduate|postgraduate|graduate|scholars program)\b", re.I)
# fellowships / programs must be aimed at students or young people, otherwise they are career fellowships
STUDENT_RE = re.compile(r"\b(students?|undergraduates?|postgraduates?|enrolled|youth|young (people|leaders|adults|professionals|changemakers|innovators)|"
                        r"aged? \d|between the ages|ages \d|under (25|30|35)|recent graduates?)\b", re.I)
# wording that means the "fellowship" is a paid job (needs CPT/OPT) -> flagged not eligible
EMPLOYMENT_RE = re.compile(
    r"\b(salary|salaried|full-time (position|role|employment|job)|paid position|employment contract|"
    r"w-2|benefits package|per annum|annual compensation)\b", re.I)
# single-country or single-region audiences
REGIONAL_RE = re.compile(
    r"\b(african|africans|nigerian|kenyan|ghanaian|ugandan|south african|ethiopian|tanzanian|rwandan|"
    r"zimbabwean|zambian|cameroonian|egyptian|moroccan|tunisian|indian|pakistani|bangladeshi|sri lankan|"
    r"nepali|nepalese|filipino|indonesian|vietnamese|malaysian|thai|cambodian|afghan|syrian|iraqi|yemeni|"
    r"jordanian|lebanese|palestinian|turkish|iranian|european|eu citizens|brazilian|mexican|colombian|peruvian|"
    r"argentin\w+|chilean|arab|commonwealth|asean|southeast asian|south asian|east african|west african|"
    r"sub-saharan|mena|latin american|caribbean|pacific islander|women in africa|russian|chinese|korean|japanese)\b", re.I)
INTL_YES_RE = re.compile(
    r"(all nationalities|any nationality|open to all|from any country|from all countries|all countries|"
    r"worldwide|around the world|international (students|applicants|participants) (are|is) (welcome|eligible|encouraged)|"
    r"open to (both )?international|regardless of nationality|citizens of (all|any) countr)", re.I)
US_ONLY_RE = re.compile(
    r"(must be|only|restricted to|limited to|open only to)[^.]{0,40}\b(u\.?s\.? citizens?|united states citizens?|"
    r"u\.?s\.? permanent residents?)", re.I)
ONLINE_RE = re.compile(r"\b(online|virtual|virtually|remote|webinar)\b", re.I)
ONLINE_BODY_RE = re.compile(r"\b(held|take[s]? place|hosted|conducted|delivered|will be) (fully |entirely |completely )?(online|virtually)\b", re.I)
FUNDED_RE = re.compile(r"fully[- ]funded|all expenses paid|fully sponsored", re.I)
STIPEND_RE = re.compile(r"\bstipend\b", re.I)
PRIZE_RE = re.compile(r"\b(prize|cash award|award of|win up to)\b", re.I)
# ". . . Forum 2027 in the UK (Fully Funded)" -> "UK";  "(Fully-funded to Berlin, Germany)" -> "Berlin, Germany"
PLACE_RE = re.compile(r"\b(?:in|to) (?:the )?([A-Z][A-Za-z.'&-]+(?:[ ,]+(?:the )?[A-Z][A-Za-z.'&-]+){0,3})")
COUNTRIES = {
    "United States", "United Kingdom", "Canada", "Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Peru",
    "Germany", "France", "Spain", "Portugal", "Italy", "Netherlands", "Belgium", "Switzerland", "Austria", "Sweden",
    "Norway", "Denmark", "Finland", "Ireland", "Poland", "Czech Republic", "Hungary", "Greece", "Turkey", "Russia",
    "Estonia", "Latvia", "Lithuania", "Croatia", "Serbia", "Romania", "Bulgaria", "Slovenia", "Slovakia", "Luxembourg",
    "Iceland", "Malta", "Cyprus", "Georgia", "Armenia", "Azerbaijan", "Ukraine",
    "China", "Japan", "South Korea", "Taiwan", "Hong Kong", "Singapore", "Malaysia", "Thailand", "Vietnam", "Indonesia",
    "Philippines", "India", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal", "Mongolia", "Kazakhstan", "Uzbekistan",
    "Australia", "New Zealand", "Fiji",
    "United Arab Emirates", "Qatar", "Saudi Arabia", "Oman", "Kuwait", "Bahrain", "Jordan", "Israel", "Lebanon", "Egypt",
    "Morocco", "Tunisia", "Algeria", "Kenya", "Nigeria", "Ghana", "Rwanda", "Uganda", "Tanzania", "Ethiopia",
    "South Africa", "Senegal", "Ivory Coast", "Cameroon", "Zambia", "Zimbabwe", "Botswana", "Namibia", "Mauritius",
}
COUNTRY_ALIASES = {
    "usa": "United States", "us": "United States", "u.s.": "United States", "u.s.a.": "United States",
    "united states": "United States", "america": "United States", "the united states": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "england": "United Kingdom", "united kingdom": "United Kingdom",
    "uae": "United Arab Emirates", "korea": "South Korea", "netherlands": "Netherlands",
}
CITY_COUNTRY = {
    "new york": "United States", "new york city": "United States", "washington": "United States", "washington dc": "United States",
    "boston": "United States", "chicago": "United States", "san francisco": "United States", "los angeles": "United States",
    "seattle": "United States", "austin": "United States", "miami": "United States", "atlanta": "United States",
    "london": "United Kingdom", "oxford": "United Kingdom", "cambridge": "United Kingdom", "edinburgh": "United Kingdom",
    "berlin": "Germany", "munich": "Germany", "bonn": "Germany", "paris": "France", "geneva": "Switzerland",
    "zurich": "Switzerland", "vienna": "Austria", "brussels": "Belgium", "amsterdam": "Netherlands", "the hague": "Netherlands",
    "stockholm": "Sweden", "copenhagen": "Denmark", "oslo": "Norway", "helsinki": "Finland", "dublin": "Ireland",
    "lisbon": "Portugal", "madrid": "Spain", "barcelona": "Spain", "rome": "Italy", "milan": "Italy", "prague": "Czech Republic",
    "warsaw": "Poland", "budapest": "Hungary", "istanbul": "Turkey", "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
    "doha": "Qatar", "tokyo": "Japan", "kyoto": "Japan", "seoul": "South Korea", "singapore": "Singapore", "bangkok": "Thailand",
    "kuala lumpur": "Malaysia", "jakarta": "Indonesia", "manila": "Philippines", "hong kong": "Hong Kong", "beijing": "China",
    "shanghai": "China", "new delhi": "India", "delhi": "India", "mumbai": "India", "nairobi": "Kenya", "lagos": "Nigeria",
    "accra": "Ghana", "kigali": "Rwanda", "cape town": "South Africa", "johannesburg": "South Africa", "cairo": "Egypt",
    "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada", "ottawa": "Canada", "mexico city": "Mexico",
    "sydney": "Australia", "melbourne": "Australia", "auckland": "New Zealand", "rio de janeiro": "Brazil", "sao paulo": "Brazil",
}
_SEEN_TITLES: set[str] = set()      # shared across the three sites within one ingest run


def reset() -> None:
    """Clear the cross-site dedupe set. ingest.run() calls this so a long-lived server process
    that ingests nightly doesn't drop every repeat post as a duplicate of last night's."""
    _SEEN_TITLES.clear()
SOCIAL_RE = re.compile(r"(facebook|twitter|x\.com|linkedin|instagram|youtube|t\.me|telegram|whatsapp|pinterest|tiktok|wp\.me|threads\.net)", re.I)


def _text(s: str | None) -> str:
    s = re.sub(r"<br\s*/?>|</(p|li|h\d|div|tr)>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def _get(url: str) -> tuple[list, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8", "replace")), {k.lower(): v for k, v in r.headers.items()}


def _month(mon: str) -> int | None:
    key = mon.lower().rstrip(".")
    return MONTHS.get(key) or MONTHS.get(key[:3])


def _parse_date(s: str) -> str | None:
    m = MDY_RE.search(s)
    if m:
        mon, d, y = m.groups()
    else:
        m = DMY_RE.search(s)
        if not m:
            return None
        d, mon, y = m.groups()
    month = _month(mon)
    if not month:
        return None
    try:
        return date(int(y), month, int(d)).isoformat()
    except ValueError:
        return None


def parse_deadline(body: str) -> tuple[str | None, str]:
    """-> (iso_date, label). Looks at every 'Deadline: ...' line and keeps the earliest future date."""
    today = date.today().isoformat()
    found: list[str] = []
    rolling = False
    for m in DEADLINE_RE.finditer(body):
        tail = m.group(1)
        iso = _parse_date(tail)
        if iso:
            found.append(iso)
        elif ROLLING_RE.search(tail):
            rolling = True
    upcoming = sorted(d for d in found if d >= today)
    if upcoming:
        return upcoming[0], "Apply by"
    if found:                       # only past dates: keep the latest so the row expires cleanly
        return max(found), "Apply by"
    if rolling:
        return None, "Rolling"
    # last resort: a date in the title ("... 2027") tells us nothing useful; leave it open
    return None, "See post"


def _category(title: str, cat_ids: list[int], site: dict) -> str | None:
    if set(cat_ids) & site["skip_categories"]:
        return None
    for cat, rx in TITLE_CATEGORY:
        if rx.search(title):
            return cat
    for cid in cat_ids:
        if cid in site["categories"]:
            return site["categories"][cid]
    return None


_COUNTRY_BY_LOWER = {c.lower(): c for c in COUNTRIES}


def _country(token: str) -> str | None:
    low = token.lower().replace(".", "").strip()
    return COUNTRY_ALIASES.get(low) or _COUNTRY_BY_LOWER.get(low)


def _location(title: str, body: str) -> tuple[str, str | None, str | None]:
    """Only trust places we can recognise (known country, known city, or 'City, Country')."""
    if ONLINE_RE.search(title) or ONLINE_BODY_RE.search(body[:3000]):
        return "online", None, None
    clean = re.sub(r"\(.*?\)|\b20\d\d\b|[-–:|].*$", " ", title)        # drop parentheticals, years, subtitles
    paren = re.findall(r"\(([^)]*)\)", title)
    cands = PLACE_RE.findall(clean) + sum((PLACE_RE.findall(p) for p in paren), [])
    for raw in reversed(cands):
        place = re.sub(r"\s+", " ", raw).strip(" ,")
        if _country(place):
            return "in_person", _country(place), None
        if place.lower() in CITY_COUNTRY:
            return "in_person", CITY_COUNTRY[place.lower()], place
        parts = [p.strip() for p in place.split(",") if p.strip()]
        if len(parts) == 2 and (_country(parts[1]) or parts[0].lower() in CITY_COUNTRY):
            return "in_person", _country(parts[1]) or CITY_COUNTRY[parts[0].lower()], parts[0]
    # no "in X" phrase: a bare country or city name anywhere in the title ("Youth Summit Malaysia 2027")
    words = re.sub(r"[^A-Za-z. ]", " ", title)
    for name in sorted(list(_COUNTRY_BY_LOWER) + list(COUNTRY_ALIASES) + list(CITY_COUNTRY), key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", words, re.I):
            if name in CITY_COUNTRY:
                return "in_person", CITY_COUNTRY[name], name.title()
            return "in_person", _country(name), None
    return "unknown", None, None


def _official_link(html_body: str, base: str) -> str | None:
    host = urllib.parse.urlparse(base).netloc.replace("www.", "")
    links = re.findall(r'href="(https?://[^"]+)"', html_body)
    links = [l for l in links if host not in l and not SOCIAL_RE.search(l)]
    return links[-1] if links else None


def parse_post(p: dict, site_key: str) -> Opportunity | None:
    site = SITES[site_key]
    title = _text(p["title"]["rendered"])
    title = re.sub(r"^\s*(apply now|call for applications|applications open)\s*[:!-]\s*", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip()
    if not title or SKIP_TITLE_RE.search(title) or REGIONAL_RE.search(title) or re.match(r"^\d+\s", title):
        return None
    # the same program is often posted by two or three of these sites in the same week
    key = re.sub(r"\b(fully|funded|sponsored|paid|trip|program(me)?|20\d\d(?:[-–/]\d{2,4})?)\b|[^a-z0-9 ]", " ", title.lower())
    key = " ".join(key.split()[:6])
    if key in _SEEN_TITLES:
        return None
    _SEEN_TITLES.add(key)
    cat_ids = [(c.get("id") or c.get("term_id")) if isinstance(c, dict) else c for c in (p.get("categories") or [])]
    if "region_categories" in site:
        regions = set(cat_ids) & site["region_categories"]
        if regions and site["required_region"] not in regions:
            return None
    category = _category(title, cat_ids, site)
    if not category:
        return None
    if category == "scholarship" and DEGREE_RE.search(title):
        return None
    html_body = p.get("content", {}).get("rendered", "") or ""
    body = _text(html_body)
    if category in ("fellowship", "research", "other") and not STUDENT_RE.search(body):
        return None
    excerpt = _text(p.get("excerpt", {}).get("rendered", "")) or body[:300]
    excerpt = re.sub(r"\s*(Read more|Continue reading|\[…\]|…)\s*$", "", excerpt).strip()
    deadline, label = parse_deadline(body)
    mode, country, city = _location(title, body)
    us_only = bool(US_ONLY_RE.search(body))
    employment = bool(EMPLOYMENT_RE.search(body)) or bool(re.search(r"\$\s?\d{2,3},\d{3}\b", title))
    intl = "yes" if (INTL_YES_RE.search(body) and not us_only) else "unknown"
    if FUNDED_RE.search(title) or FUNDED_RE.search(body) or category == "scholarship":
        comp, note = "funding", "Fully funded" if FUNDED_RE.search(title + " " + body) else "Scholarship / funding"
    elif STIPEND_RE.search(body):
        comp, note = "stipend", "Stipend"
    elif PRIZE_RE.search(title) or (category == "competition" and PRIZE_RE.search(body)):
        comp, note = "prize", "Prizes"
    else:
        comp, note = "none", None
    media = ((p.get("_embedded") or {}).get("wp:featuredmedia") or [{}])[0]
    image = media.get("source_url") if isinstance(media, dict) else None
    official = _official_link(html_body, site["base"])
    tags = [category, site["label"]]
    if FUNDED_RE.search(title + body):
        tags.append("fully funded")
    for word in ("leadership", "climate", "AI", "policy", "entrepreneurship", "women", "youth", "journalism", "design", "health"):
        if re.search(rf"\b{word}\b", title, re.I):
            tags.append(word.lower())
    desc = excerpt[:400]
    if official:
        desc += f" Official page: {official}"
    return Opportunity(
        source=site_key,
        source_id=str(p["id"]),
        url=p["link"],
        title=title,
        category=category,
        organizer=None,
        description=desc,
        tags=tags,
        image_url=image if isinstance(image, str) and image.startswith("http") else None,
        deadline=deadline,
        deadline_label=label,
        location_mode=mode,
        country=country,
        city=city,
        compensation_type=comp,
        compensation_note=note,
        requires_us_citizen=us_only,
        requires_work_auth=employment,
        open_to_intl=intl,
        eligibility_note=("Reads like a salaried position, which needs CPT/OPT." if employment else
            None if intl == "yes" else
            f"Listed by {site['label']}; nationality rules aren't stated up front. Read the eligibility section of the post."),
    )


def fetch_site(site_key: str) -> list[Opportunity]:
    site = SITES[site_key]
    after = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00")
    cats = ",".join(str(c) for c in site["categories"])
    out: list[Opportunity] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        url = (f"{site['base']}/wp-json/wp/v2/posts?per_page={PER_PAGE}&page={page}&after={after}"
               f"&categories={cats}&_embed=wp:featuredmedia"
               f"&_fields=id,title,link,date,categories,excerpt,content,_links,_embedded")
        posts, headers = _get(url)
        for p in posts:
            o = parse_post(p, site_key)
            if o and o.source_id not in seen:
                seen.add(o.source_id)
                out.append(o)
        if page >= int(headers.get("x-wp-totalpages") or 1):
            break
    return out


def fetch_opportunitydesk() -> list[Opportunity]:
    return fetch_site("opportunitydesk")


def fetch_oyaop() -> list[Opportunity]:
    return fetch_site("oyaop")


def fetch_opportunitiescorners() -> list[Opportunity]:
    return fetch_site("opportunitiescorners")
