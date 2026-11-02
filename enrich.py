"""Read each opportunity's official page with Claude and record what it says about eligibility.

    export ANTHROPIC_API_KEY=sk-ant-...
    python3 enrich.py                 # rows whose nationality rule is unknown, oldest first (max 300)
    python3 enrich.py --limit 20      # try a few first
    python3 enrich.py --dry-run       # fetch pages and show what would be sent, no API calls
    python3 enrich.py --source devpost --recheck-days 30

For every candidate row this fetches the organizer's page (for aggregator-blog rows, the "Official
page:" link inside the post rather than the blog post itself), strips it to text, and asks Claude
one structured question: does this page state who may take part? The answer is stored in
`eligibility_checks` and merged into the opportunity by models.apply_check(), which also runs after
every scraper upsert, so a nightly re-scrape can't erase it.

Rules the model is held to: quote only sentences that appear verbatim on the page, answer "unknown"
when nothing is stated, and never infer eligibility from the organizer's country. Low-confidence
answers are stored but not applied to the card.
"""
from __future__ import annotations

import argparse
import html as htmllib
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from models import apply_check, connect
from sources.http import UA, _ssl_context

MODEL = os.environ.get("ENRICH_MODEL", "claude-opus-5")
MAX_CHARS = 40_000             # ~10k tokens of page text per call
HEAD_CHARS = 16_000            # always keep the top of the page ...
WINDOW = 1_500                 # ... plus this much around each eligibility keyword further down
KEYWORD_RE = re.compile(r"eligib|citizen|nationalit|international|resident|who can (apply|participate|enter)|"
                        r"open to|visa|work authori|enrolled|must be", re.I)
OFFICIAL_RE = re.compile(r"Official page: (https?://\S+)")
# Hosts that never yield readable text: bot-blocked (403) or fully client-rendered apps. Rows whose
# only link is here are recorded as unreachable without a request.
SKIP_HOSTS = ("devpost.com", "kaggle.com", "unstop.com")   # devpost 403s; kaggle/unstop ship an empty <app-root>;

SYSTEM = """You read the official web page of a hackathon, competition, conference, fellowship, scholarship, research program or similar opportunity, and report what it says about who may take part. The reader is an international student studying in the United States on an F-1 visa (a citizen of some other country, legally present in the US, not allowed to take paid employment off campus without separate authorization).

Answer only from the page text you are given.
- `international_ok`: "yes" if the page explicitly says the opportunity is open to all nationalities, to international participants, to people worldwide, or states there is no citizenship requirement; or if its only stated requirements are things an F-1 student in the US can meet (e.g. "enrolled at a US university", "age 18+"). "no" if it requires US citizenship or permanent residency, or is limited to residents or citizens of a region that excludes someone living in the US. "depends" if it is limited to citizens of specific countries (some readers will qualify, others not): name them in `note`. "unknown" if the page does not state any nationality, citizenship or residency rule.
- `restriction`: the kind of rule you found: "none", "us_citizen_or_pr", "specific_nationalities", "region_residents", "other", or "none" when unknown.
- `audience`: who the page is for: "students" (enrolled undergraduate or graduate students), "high_school", "professionals" (working adults, faculty, mid-career), "anyone", or "unknown".
- `quote`: one sentence copied verbatim from the page that supports your answer. Leave it empty if nothing on the page addresses the question. Never paraphrase or invent a quote.
- `note`: one plain-English sentence summarising the rule, or an empty string.
- `is_paid_job`: true only if the page describes employment paid as salary or hourly wages (a job, paid internship, "full-time position"). Prize money, scholarships, stipends for fellowships and research, and travel grants are not jobs.
- `confidence`: "high" if the page states the rule plainly; "medium" if you inferred it from clear but indirect wording; "low" if the page is thin, off-topic, an error page, or you are guessing.

Do not infer eligibility from where the organizer is based or from the opportunity's name. If the page is not about this opportunity at all (login wall, 404, unrelated content), answer unknown with low confidence."""


class Check(BaseModel):
    international_ok: Literal["yes", "no", "depends", "unknown"]
    restriction: Literal["none", "us_citizen_or_pr", "specific_nationalities", "region_residents", "other"]
    audience: Literal["students", "high_school", "professionals", "anyone", "unknown"]
    quote: str = Field(description="Verbatim sentence from the page, or empty")
    note: str = Field(description="One-sentence summary of the rule, or empty")
    is_paid_job: bool
    confidence: Literal["high", "medium", "low"]


# ---------------------------------------------------------------- fetch + clean

def fetch_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        ctype = r.headers.get("content-type", "")
        raw = r.read(3_000_000)
    if "html" not in ctype and "text" not in ctype:
        return ""
    return html_to_text(raw.decode("utf-8", "replace"))


def html_to_text(doc: str) -> str:
    doc = re.sub(r"<(script|style|noscript|svg|head)\b.*?</\1>", " ", doc, flags=re.I | re.S)
    doc = re.sub(r"<!--.*?-->", " ", doc, flags=re.S)
    doc = re.sub(r"<br\s*/?>|</(p|li|h\d|div|tr|section|article|dd|dt|blockquote)>", "\n", doc, flags=re.I)
    doc = re.sub(r"<[^>]+>", " ", doc)
    doc = htmllib.unescape(doc).replace("\xa0", " ")
    doc = re.sub(r"[ \t\r\f\v]+", " ", doc)
    doc = re.sub(r" *\n *", "\n", doc)
    return re.sub(r"\n{3,}", "\n\n", doc).strip()


def focus(text: str, cap: int = MAX_CHARS) -> str:
    """Long pages: keep the head plus windows around eligibility keywords, so the sentence that
    matters survives truncation."""
    if len(text) <= cap:
        return text
    pieces = [text[:HEAD_CHARS]]
    budget = cap - HEAD_CHARS
    last_end = HEAD_CHARS
    for m in KEYWORD_RE.finditer(text, HEAD_CHARS):
        start, end = max(m.start() - WINDOW, last_end), min(m.end() + WINDOW, len(text))
        if start >= end:
            continue
        chunk = text[start:end]
        if len(chunk) > budget:
            break
        pieces.append(("\n[...]\n" if start > last_end else "") + chunk)
        budget -= len(chunk)
        last_end = end
    return "".join(pieces)


def page_candidates(row) -> list[str]:
    """Aggregator rows link to a blog post that in turn links to the organizer; try the organizer first."""
    urls: list[str] = []
    m = OFFICIAL_RE.search(row["description"] or "")
    if m:
        urls.append(m.group(1).rstrip(".,)"))
    if row["url"] not in urls:
        urls.append(row["url"])
    return urls


def read_page(row) -> tuple[str | None, str]:
    """-> (url_used, text). Falls back through candidates; empty text if none worked."""
    for url in page_candidates(row):
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
        if any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
            continue
        try:
            text = fetch_text(url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
            print(f"    fetch failed {url}: {e}", file=sys.stderr)
            continue
        if len(text) >= 300:
            return url, focus(text)
    return None, ""


# ---------------------------------------------------------------- ask Claude

def ask(client, row, text: str) -> tuple[Check, dict]:
    import anthropic  # local import so --dry-run works without the SDK installed

    user = (f"Opportunity: {row['title']}\nOrganizer: {row['organizer'] or 'unknown'}\n"
            f"Type: {row['category']}\n\nPage text:\n<<<\n{text}\n>>>")
    try:
        resp = client.messages.parse(
            model=MODEL,
            max_tokens=2000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user}],
            output_format=Check,
        )
    except anthropic.RateLimitError as e:
        wait = int(e.response.headers.get("retry-after", "30"))
        print(f"    rate limited, sleeping {wait}s", file=sys.stderr)
        time.sleep(wait)
        return ask(client, row, text)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        return Check(international_ok="unknown", restriction="none", audience="unknown", quote="", note="",
                     is_paid_job=False, confidence="low"), {"usage": resp.usage.to_dict() if resp.usage else {}}
    return resp.parsed_output, {"usage": resp.usage.to_dict() if resp.usage else {}}


# ---------------------------------------------------------------- main loop

def candidates(conn, *, source: str | None, recheck_days: int, limit: int, all_rows: bool):
    stale = (datetime.now(timezone.utc) - timedelta(days=recheck_days)).isoformat(timespec="seconds")
    sql = """
        SELECT o.* FROM opportunities o
        LEFT JOIN eligibility_checks c ON c.source = o.source AND c.source_id = o.source_id
        WHERE o.status = 'active' AND o.verified = 0 AND o.f1_flag IN ('likely_ok', 'check_dso')
          AND (c.source_id IS NULL OR c.checked_at < %(stale)s)
    """
    params: dict = {"stale": stale, "limit": limit}
    if not all_rows:
        sql += " AND o.open_to_intl = 'unknown'"
    if source:
        sql += " AND o.source = %(source)s"
        params["source"] = source
    sql += " ORDER BY (o.deadline IS NULL), o.deadline, o.id LIMIT %(limit)s"
    return conn.execute(sql, params).fetchall()


UPSERT_CHECK = """
    INSERT INTO eligibility_checks
        (source, source_id, checked_url, fetch_ok, international_ok, restriction, audience, quote, note,
         is_paid_job, confidence, model, checked_at)
    VALUES (%(source)s, %(source_id)s, %(checked_url)s, %(fetch_ok)s, %(international_ok)s, %(restriction)s,
            %(audience)s, %(quote)s, %(note)s, %(is_paid_job)s, %(confidence)s, %(model)s, %(checked_at)s)
    ON CONFLICT (source, source_id) DO UPDATE SET
        checked_url=EXCLUDED.checked_url, fetch_ok=EXCLUDED.fetch_ok, international_ok=EXCLUDED.international_ok,
        restriction=EXCLUDED.restriction, audience=EXCLUDED.audience, quote=EXCLUDED.quote, note=EXCLUDED.note,
        is_paid_job=EXCLUDED.is_paid_job, confidence=EXCLUDED.confidence, model=EXCLUDED.model,
        checked_at=EXCLUDED.checked_at"""


def save_check(conn, row, *, url, fetch_ok: bool, check=None, now: str) -> None:
    conn.execute(UPSERT_CHECK, {
        "source": row["source"], "source_id": row["source_id"], "checked_url": url, "fetch_ok": int(fetch_ok),
        "international_ok": check.international_ok if check else "unknown",
        "restriction": check.restriction if check else None, "audience": check.audience if check else None,
        "quote": (check.quote.strip() or None) if check else None, "note": (check.note.strip() or None) if check else None,
        "is_paid_job": int(check.is_paid_job) if check else 0, "confidence": check.confidence if check else None,
        "model": MODEL, "checked_at": now})


def run(*, limit: int = 300, source: str | None = None, recheck_days: int = 90, dry_run: bool = False,
        all_rows: bool = False, log=print) -> dict:
    with connect() as conn:
        rows = candidates(conn, source=source, recheck_days=recheck_days, limit=limit, all_rows=all_rows)
    log(f"{len(rows)} rows to check" + (" (dry run)" if dry_run else f" with {MODEL}"))
    client = None
    if not dry_run and rows:
        import anthropic
        client = anthropic.Anthropic()            # ANTHROPIC_API_KEY or `ant auth login` profile
    tally = {"checked": 0, "unreachable": 0, "yes": 0, "no": 0, "depends": 0, "unknown": 0, "applied": 0,
             "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in rows:
        url, text = read_page(row)
        if not text:
            tally["unreachable"] += 1
            log(f"  [{row['source']}] {row['title'][:60]!r}: page unreachable")
            if not dry_run:
                with connect() as conn:
                    save_check(conn, row, url=row["url"], fetch_ok=False, now=now)
                    apply_check(conn, row["source"], row["source_id"])
            continue
        if dry_run:
            log(f"  [{row['source']}] {row['title'][:60]!r}: {len(text)} chars from {url}")
            continue
        check, meta = ask(client, row, text)
        u = meta.get("usage") or {}
        tally["input_tokens"] += u.get("input_tokens") or 0
        tally["cache_read_tokens"] += u.get("cache_read_input_tokens") or 0
        tally["output_tokens"] += u.get("output_tokens") or 0
        with connect() as conn:
            save_check(conn, row, url=url, fetch_ok=True, check=check, now=now)
            changed = apply_check(conn, row["source"], row["source_id"])
        tally["checked"] += 1
        tally[check.international_ok] += 1
        tally["applied"] += int(changed)
        log(f"  [{row['source']}] {row['title'][:60]!r}: {check.international_ok}/{check.confidence}"
            + (f" paid-job" if check.is_paid_job else "") + (f" · “{check.quote[:90]}”" if check.quote else ""))
    log("done: " + ", ".join(f"{k}={v}" for k, v in tally.items()))
    return tally


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--source", help="only rows from this source (e.g. devpost)")
    ap.add_argument("--recheck-days", type=int, default=90, help="re-read pages checked longer ago than this")
    ap.add_argument("--all", action="store_true", help="also re-verify rows the scrapers already marked yes/no")
    ap.add_argument("--dry-run", action="store_true", help="fetch pages, show sizes, call nothing")
    a = ap.parse_args()
    run(limit=a.limit, source=a.source, recheck_days=a.recheck_days, dry_run=a.dry_run, all_rows=a.all)


if __name__ == "__main__":
    main()
